"""Minimal z/OS JCL reader: just enough for CardDemo's batch jobs.

Covers JOB/EXEC/DD statements, continuation lines, comments, in-stream data
(DD * / DD DATA) and the IDCAMS commands used to define and load CardDemo's
datasets.  Anything else (PROC expansion, symbolic parameters, IF/THEN/ELSE,
INCLUDE, concatenated DDs) raises UnsupportedJCL rather than being guessed at.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class UnsupportedJCL(Exception):
    pass


# ---------------------------------------------------------------- operands --

def _cut_operands(s: str) -> str:
    """Operand field ends at the first blank outside quotes/parentheses."""
    depth, quoted, out = 0, False, []
    for ch in s:
        if quoted:
            out.append(ch)
            if ch == "'":
                quoted = False
            continue
        if ch == "'":
            quoted = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == " " and depth == 0:
            break
        out.append(ch)
    return "".join(out)


def split_top(s: str, sep: str = ",") -> list[str]:
    """Split on `sep` at parenthesis depth 0, outside quotes."""
    parts, depth, quoted, cur = [], 0, False, []
    for ch in s:
        if quoted:
            cur.append(ch)
            if ch == "'":
                quoted = False
            continue
        if ch == "'":
            quoted = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur))
    return parts


def sublist(value: str) -> list[str]:
    """'(NEW,CATLG,DELETE)' -> ['NEW','CATLG','DELETE'];  'SHR' -> ['SHR']."""
    v = value.strip()
    if v.startswith("(") and v.endswith(")"):
        return [p.strip() for p in split_top(v[1:-1])]
    return [v]


def keyword_dict(items: list[str]) -> tuple[list[str], dict[str, str]]:
    positional, keywords = [], {}
    for it in items:
        if not it:
            positional.append(it)
            continue
        m = re.match(r"^([A-Z0-9@#$.]+)=(.*)$", it, re.S)
        if m and not it.startswith("("):
            keywords[m.group(1)] = m.group(2)
        else:
            positional.append(it)
    return positional, keywords


# -------------------------------------------------------------- statements --

@dataclass
class DD:
    name: str
    positional: list[str]
    params: dict[str, str]
    instream: list[str] | None = None
    line: int = 0

    @property
    def dsn(self) -> str | None:
        v = self.params.get("DSN") or self.params.get("DSNAME")
        return v.strip() if v else None

    @property
    def sysout(self) -> bool:
        return "SYSOUT" in self.params

    @property
    def dummy(self) -> bool:
        return "DUMMY" in self.positional

    def disp(self) -> tuple[str, str, str]:
        """(status, normal, abnormal) with z/OS defaults filled in."""
        raw = sublist(self.params["DISP"]) if "DISP" in self.params else []
        raw += [""] * (3 - len(raw))
        status = raw[0] or "NEW"
        normal = raw[1] or ("DELETE" if status == "NEW" else "KEEP")
        abnormal = raw[2] or normal
        return status, normal, abnormal

    def dcb(self) -> dict[str, str]:
        out = {}
        if "DCB" in self.params:
            _, kw = keyword_dict(sublist(self.params["DCB"]))
            out.update(kw)
        for k in ("LRECL", "RECFM", "BLKSIZE", "DSORG"):
            if k in self.params:
                out[k] = self.params[k]
        return out


@dataclass
class Step:
    name: str
    pgm: str
    params: dict[str, str]
    dds: list[DD] = field(default_factory=list)
    line: int = 0

    def dd(self, name: str) -> DD | None:
        return next((d for d in self.dds if d.name == name), None)


@dataclass
class Job:
    name: str
    path: Path
    steps: list[Step]
    params: dict[str, str]


def _statements(text: str):
    """Yield (lineno, name, op, operands, instream_lines|None)."""
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        lineno = i + 1
        raw = lines[i].rstrip("\r")
        i += 1
        if not raw.startswith("//") or raw.startswith("//*"):
            continue                        # comment, delimiter or stray data
        body = raw[:71][2:]
        if not body.strip():
            continue                        # null statement
        name = ""
        if body[0] != " ":
            name = body.split(" ", 1)[0]
            body = body[len(name):]
        body = body.lstrip()
        op, _, rest = body.partition(" ")
        operands = _cut_operands(rest.lstrip())
        # continuation: operand field ends with a comma
        while operands.endswith(",") and i < n:
            nxt = lines[i].rstrip("\r")
            if not nxt.startswith("//") or nxt.startswith("//*"):
                if nxt.startswith("//*"):
                    i += 1
                    continue
                break
            i += 1
            operands += _cut_operands(nxt[:71][2:].lstrip())
        instream = None
        if op == "DD":
            first = split_top(operands)[0] if operands else ""
            if first in ("*", "DATA"):
                instream = []
                while i < n:
                    d = lines[i].rstrip("\r")
                    if d.startswith("/*"):
                        i += 1
                        break
                    if d.startswith("//"):
                        break
                    instream.append(d[:72] if first == "*" else d)
                    i += 1
        yield lineno, name, op, operands, instream


def parse_jcl(path: Path) -> Job:
    text = path.read_text(encoding="latin-1")
    job: Job | None = None
    step: Step | None = None
    for lineno, name, op, operands, instream in _statements(text):
        items = split_top(operands) if operands else []
        positional, kw = keyword_dict(items)
        if op == "JOB":
            job = Job(name=name, path=path, steps=[], params=kw)
        elif op == "EXEC":
            if job is None:
                raise UnsupportedJCL(f"{path}:{lineno}: EXEC before JOB")
            if "PGM" not in kw:
                raise UnsupportedJCL(f"{path}:{lineno}: EXEC of a PROC is not supported")
            step = Step(name=name, pgm=kw["PGM"], params=kw, line=lineno)
            job.steps.append(step)
        elif op == "DD":
            if step is None:
                raise UnsupportedJCL(f"{path}:{lineno}: DD outside a step (JOBLIB?)")
            if not name:
                raise UnsupportedJCL(f"{path}:{lineno}: concatenated DD not supported")
            step.dds.append(DD(name=name, positional=positional, params=kw,
                               instream=instream, line=lineno))
        elif op in ("PROC", "PEND", "INCLUDE", "JCLLIB", "SET", "IF", "ELSE", "ENDIF"):
            raise UnsupportedJCL(f"{path}:{lineno}: {op} statement not supported")
        else:
            raise UnsupportedJCL(f"{path}:{lineno}: unknown statement {op!r}")
    if job is None:
        raise UnsupportedJCL(f"{path}: no JOB statement")
    return job


# ------------------------------------------------------------------ IDCAMS --

def idcams_commands(lines: list[str]) -> list[str]:
    """Join IDCAMS continuation lines ('-' or '+' at end) into commands."""
    cmds, cur = [], ""
    for ln in lines:
        s = re.sub(r"/\*.*?\*/", " ", ln[:72]).rstrip()
        if not s.strip():
            continue
        if s.endswith("-") or s.endswith("+"):
            cur += s[:-1] + " "
            continue
        cur += s
        cmds.append(" ".join(cur.split()))
        cur = ""
    if cur.strip():
        cmds.append(" ".join(cur.split()))
    return cmds
