"""Build legacy programs, stubs and helper utilities into build/.

Every artefact is rebuilt when the hash of its inputs changes: source,
the copybooks it COPYs, and the exact compiler command line.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from .catalog import LEGACY_APP, REPO

BUILD = REPO / "build"
BIN = BUILD / "bin"
LIB = BUILD / "lib"
UTIL = BUILD / "util"
LOGS = BUILD / "logs"
CBL_DIR = LEGACY_APP / "cbl"
CPY_DIR = LEGACY_APP / "cpy"
STUB_DIR = REPO / "runner" / "stubs"
CLOCK_SRC = REPO / "runner" / "clock" / "frozen_clock.c"

# docs/legacy-setup.md W2 (dialect), W4 (TRUNC), W5 (sign representation)
COBC_FLAGS = ["-x", "-std=ibm", "-fbinary-truncate", "-fsign=EBCDIC"]
CC_FLAGS = ["-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Werror"]

# Ubuntu's libcob CFLAGS redefine _FORTIFY_SOURCE: harmless (W3).
_NOISE = re.compile(r'^(<command-line>: (warning: "_FORTIFY_SOURCE" redefined|'
                    r'note: this is the location of the previous definition))$')

ABEND_EXIT = 222        # must match runner/stubs/CEE3ABD.c


class BuildError(Exception):
    pass


def _sha(*parts: bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(hashlib.sha256(p).digest())
    return h.hexdigest()


def _copybooks(src: Path, seen: set[str] | None = None) -> list[Path]:
    """Copybooks a program COPYs, transitively, in a stable order."""
    seen = set() if seen is None else seen
    out = []
    text = src.read_text(encoding="latin-1")
    for line in text.splitlines():
        code = line[6:72] if len(line) > 6 else ""
        if line[6:7] in ("*", "/"):
            continue
        for m in re.finditer(r"\bCOPY\s+['\"]?([A-Z0-9#@$-]+)['\"]?", code):
            name = m.group(1)
            if name in seen:
                continue
            seen.add(name)
            p = CPY_DIR / f"{name}.cpy"
            if not p.exists():
                raise BuildError(f"{src.name}: COPY {name}: {p} not found")
            out.append(p)
            out += _copybooks(p, seen)
    return out


def _run_build(name: str, cmd: list[str], inputs: list[Path], out: Path) -> dict:
    stamp = out.with_name(out.name + ".inputs")
    key = _sha(" ".join(cmd).encode(), *(p.read_bytes() for p in inputs))
    if out.exists() and stamp.exists() and stamp.read_text() == key:
        log = LOGS / f"{name}.log"
        warnings = log.read_text().splitlines() if log.exists() else []
        return {"output": out, "inputs_sha256": key, "warnings": warnings, "rebuilt": False}
    out.parent.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    lines = [l for l in (proc.stdout + proc.stderr).splitlines() if not _NOISE.match(l)]
    (LOGS / f"{name}.log").write_text("\n".join(lines) + ("\n" if lines else ""))
    if proc.returncode != 0:
        raise BuildError(f"{name}: build failed (rc={proc.returncode})\n" + "\n".join(lines))
    stamp.write_text(key)
    return {"output": out, "inputs_sha256": key, "warnings": lines, "rebuilt": True}


def compile_program(pgm: str) -> dict:
    src = CBL_DIR / f"{pgm}.cbl"
    if not src.exists():
        raise BuildError(f"program {pgm}: {src} not found")
    out = BIN / pgm
    cmd = ["cobc", *COBC_FLAGS, "-I", str(CPY_DIR.relative_to(REPO)),
           "-o", str(out.relative_to(REPO)), str(src.relative_to(REPO))]
    info = _run_build(pgm, cmd, [src, *_copybooks(src)], out)
    info["source"] = str(src.relative_to(REPO))
    info["copybooks"] = [p.name for p in _copybooks(src)]
    info["command"] = " ".join(cmd)
    return info


def build_stubs() -> dict[str, dict]:
    """runner/stubs/NAME.c -> build/lib/NAME.so (found via COB_LIBRARY_PATH)."""
    out = {}
    for src in sorted(STUB_DIR.glob("*.c")):
        so = LIB / f"{src.stem}.so"
        cmd = ["gcc", *CC_FLAGS, "-o", str(so.relative_to(REPO)), str(src.relative_to(REPO))]
        out[src.stem] = _run_build(f"stub-{src.stem}", cmd, [src], so)
    return out


def build_clock() -> dict:
    so = LIB / "libfrozenclock.so"
    cmd = ["gcc", *CC_FLAGS, "-o", str(so.relative_to(REPO)), str(CLOCK_SRC.relative_to(REPO)), "-ldl"]
    info = _run_build("frozen-clock", cmd, [CLOCK_SRC], so)
    check_src = UTIL / "CLOCKCHK.cbl"
    _write_if_changed(check_src, _cobol([
        "IDENTIFICATION DIVISION.",
        "PROGRAM-ID. CLOCKCHK.",
        "PROCEDURE DIVISION.",
        "    DISPLAY FUNCTION CURRENT-DATE",
        "    GOBACK.",
    ]))
    chk = UTIL / "CLOCKCHK"
    _run_build("CLOCKCHK", ["cobc", *COBC_FLAGS, "-o", str(chk.relative_to(REPO)),
                            str(check_src.relative_to(REPO))], [check_src], chk)
    info["check_program"] = chk
    return info


# ------------------------------------------------- KSDS load / unload utils --

def _cobol(lines: list[str]) -> str:
    """Fixed-format source: code starts in Area A (column 8)."""
    out = []
    for ln in lines:
        if len(ln) + 7 > 72:
            raise BuildError(f"generated COBOL line too long: {ln!r}")
        out.append(" " * 7 + ln)
    return "\n".join(out) + "\n"


def _write_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def ksds_utility(mode: str, reclen: int, keyoff: int, keylen: int) -> Path:
    """Generate and compile a KSDS loader ('load': FB seq -> KSDS, like IDCAMS
    REPRO into an empty cluster: ascending keys, no duplicates) or unloader
    ('unload': KSDS -> FB seq in key order)."""
    if mode not in ("load", "unload"):
        raise ValueError(mode)
    name = f"KS{mode.upper()}_{reclen}_{keyoff}_{keylen}"
    rec = []
    if keyoff:
        rec.append(f"    05  FILLER            PIC X({keyoff}).")
    rec.append(f"    05  KSDS-KEY          PIC X({keylen}).")
    rest = reclen - keyoff - keylen
    if rest < 0:
        raise BuildError(f"{name}: key beyond record")
    if rest:
        rec.append(f"    05  FILLER            PIC X({rest}).")
    if mode == "load":
        open_ = ["OPEN INPUT SEQ-FILE", "PERFORM CHK-SEQ",
                 "OPEN OUTPUT KSDS-FILE", "PERFORM CHK-KSDS"]
        loop = ["    READ SEQ-FILE",
                "        AT END MOVE 'Y' TO WS-EOF",
                "        NOT AT END",
                "            WRITE KSDS-REC FROM SEQ-REC",
                "            PERFORM CHK-KSDS",
                "            ADD 1 TO WS-COUNT",
                "    END-READ",
                "    IF SEQ-ST NOT = '00' AND SEQ-ST NOT = '10'",
                "        PERFORM CHK-SEQ",
                "    END-IF"]
    else:
        open_ = ["OPEN INPUT KSDS-FILE", "PERFORM CHK-KSDS",
                 "OPEN OUTPUT SEQ-FILE", "PERFORM CHK-SEQ"]
        loop = ["    READ KSDS-FILE NEXT RECORD",
                "        AT END MOVE 'Y' TO WS-EOF",
                "        NOT AT END",
                "            WRITE SEQ-REC FROM KSDS-REC",
                "            PERFORM CHK-SEQ",
                "            ADD 1 TO WS-COUNT",
                "    END-READ",
                "    IF KSDS-ST NOT = '00' AND KSDS-ST NOT = '10'",
                "        PERFORM CHK-KSDS",
                "    END-IF"]
    src = _cobol([
        "IDENTIFICATION DIVISION.",
        f"PROGRAM-ID. KS{mode.upper()}.",
        "ENVIRONMENT DIVISION.",
        "INPUT-OUTPUT SECTION.",
        "FILE-CONTROL.",
        "    SELECT SEQ-FILE ASSIGN TO SEQFILE",
        "        ORGANIZATION IS SEQUENTIAL",
        "        FILE STATUS IS SEQ-ST.",
        "    SELECT KSDS-FILE ASSIGN TO KSDSFILE",
        "        ORGANIZATION IS INDEXED",
        "        ACCESS MODE IS SEQUENTIAL",
        "        RECORD KEY IS KSDS-KEY",
        "        FILE STATUS IS KSDS-ST.",
        "DATA DIVISION.",
        "FILE SECTION.",
        "FD  SEQ-FILE.",
        f"01  SEQ-REC               PIC X({reclen}).",
        "FD  KSDS-FILE.",
        "01  KSDS-REC.",
        *rec,
        "WORKING-STORAGE SECTION.",
        "01  SEQ-ST                PIC XX.",
        "01  KSDS-ST               PIC XX.",
        "01  WS-EOF                PIC X VALUE 'N'.",
        "01  WS-COUNT              PIC 9(9) VALUE 0.",
        "PROCEDURE DIVISION.",
        *("    " + s for s in open_),
        "    PERFORM UNTIL WS-EOF = 'Y'",
        *("    " + s for s in loop),
        "    END-PERFORM",
        "    CLOSE SEQ-FILE KSDS-FILE",
        f"    DISPLAY 'KS{mode.upper()} RECORDS=' WS-COUNT",
        "    MOVE 0 TO RETURN-CODE",
        "    STOP RUN.",
        "CHK-SEQ.",
        "    IF SEQ-ST NOT = '00'",
        "        DISPLAY 'SEQFILE STATUS=' SEQ-ST ' AFTER ' WS-COUNT",
        "        MOVE 12 TO RETURN-CODE",
        "        STOP RUN",
        "    END-IF.",
        "CHK-KSDS.",
        "    IF KSDS-ST NOT = '00'",
        "        DISPLAY 'KSDSFILE STATUS=' KSDS-ST ' AFTER ' WS-COUNT",
        "        MOVE 12 TO RETURN-CODE",
        "        STOP RUN",
        "    END-IF.",
    ])
    cbl = UTIL / f"{name}.cbl"
    _write_if_changed(cbl, src)
    exe = UTIL / name
    _run_build(name, ["cobc", *COBC_FLAGS, "-o", str(exe.relative_to(REPO)),
                      str(cbl.relative_to(REPO))], [cbl], exe)
    return exe


def toolchain() -> dict[str, str]:
    def first(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines()[0].strip()
        except (OSError, IndexError):
            return "unknown"
    info = {"cobc": first(["cobc", "--version"]), "gcc": first(["gcc", "--version"])}
    for line in subprocess.run(["cobc", "--info"], capture_output=True, text=True).stdout.splitlines():
        if "indexed file handler" in line:
            info["indexed_handler"] = line.split(":", 1)[1].strip()
    return info
