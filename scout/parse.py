"""Parsers for mainframe artifacts: COBOL programs, copybooks, JCL, CICS CSD.

Deliberately regex/heuristic based: the goal is a fast, explainable inventory
an FDE can trust on day one, not a full COBOL compiler front end. Anything
inferred (rather than read literally) is marked so downstream code can label it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

COBOL_EXT = {".cbl", ".cob"}
COPY_EXT = {".cpy"}
JCL_EXT = {".jcl", ".prc"}
CSD_EXT = {".csd"}

UTILITY_PGMS = {
    "IDCAMS": "VSAM define/delete/repro",
    "SORT": "DFSORT",
    "ICETOOL": "DFSORT ICETOOL",
    "IEBGENER": "sequential copy",
    "IEFBR14": "no-op (dataset alloc/delete)",
    "IKJEFT01": "TSO batch (often DB2 run)",
    "IKJEFT1B": "TSO batch (often DB2 run)",
    "SDSF": "spool ops",
    "FTP": "file transfer",
    "DFSRRC00": "IMS batch region",
    "IEBCOPY": "PDS copy",
}


def _norm_dsn(dsn: str) -> str:
    """Strip GDG generation and PDS member: A.B.C(+1) -> A.B.C"""
    dsn = dsn.strip().strip("'")
    return re.sub(r"\(.*\)$", "", dsn)


def cobol_code_lines(text: str) -> list[str]:
    """Fixed-format COBOL: cols 1-6 sequence, col 7 indicator, 8-72 code."""
    out = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if len(line) >= 7 and line[6] in "*/":
            continue
        code = line[7:72] if len(line) > 7 else ""
        if code.strip():
            out.append(code.upper())
    return out


@dataclass
class FileDecl:
    name: str            # COBOL file name (SELECT x)
    ddname: str          # ASSIGN TO
    organization: str    # SEQUENTIAL / INDEXED / RELATIVE
    access: str
    modes: set = field(default_factory=set)   # INPUT/OUTPUT/I-O/EXTEND


@dataclass
class Program:
    name: str
    path: str
    module: str                      # which sub-app folder it came from
    loc: int = 0
    kind: str = "batch"              # batch | cics | ims | db2-batch | ...
    copybooks: list = field(default_factory=list)
    calls: list = field(default_factory=list)          # static CALL 'X'
    xctl_literal: list = field(default_factory=list)   # XCTL/LINK PROGRAM('X')
    xctl_inferred: list = field(default_factory=list)  # via VALUE literals
    files: dict = field(default_factory=dict)          # name -> FileDecl
    cics_files: list = field(default_factory=list)     # CICS FILE/DATASET names
    cics_file_ops: list = field(default_factory=list)  # (verb, file)
    cics_cmds: dict = field(default_factory=dict)
    sql_tables: list = field(default_factory=list)
    uses_ims: bool = False
    uses_mq: bool = False
    bms_maps: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    literals: set = field(default_factory=set)


@dataclass
class Copybook:
    name: str
    path: str
    loc: int
    fields: int
    comp3: int
    redefines: int
    occurs: int
    nested_copies: list = field(default_factory=list)


@dataclass
class DD:
    ddname: str
    dsn: str | None
    disp: str
    inline: bool = False


@dataclass
class Step:
    name: str
    pgm: str | None
    proc: str | None
    parm: str = ""
    dds: list = field(default_factory=list)
    inline_text: str = ""


@dataclass
class Job:
    name: str
    path: str
    module: str
    description: str
    steps: list = field(default_factory=list)


# ---------------------------------------------------------------- copybooks
def parse_copybook(path: Path) -> Copybook:
    lines = cobol_code_lines(path.read_text(errors="replace"))
    body = "\n".join(lines)
    return Copybook(
        name=path.stem.upper(),
        path=str(path),
        loc=len(lines),
        fields=len(re.findall(r"^\s*\d{2}\s+[A-Z0-9-]+", body, re.M)),
        comp3=len(re.findall(r"COMP-3|PACKED-DECIMAL", body)),
        redefines=len(re.findall(r"\bREDEFINES\b", body)),
        occurs=len(re.findall(r"\bOCCURS\b", body)),
        nested_copies=re.findall(r"\bCOPY\s+'?([A-Z0-9@#$-]+)'?", body),
    )


# ---------------------------------------------------------------- programs
_CICS_RE = re.compile(r"EXEC\s+CICS\s+(.*?)END-EXEC", re.S)
_SQL_RE = re.compile(r"EXEC\s+SQL\s+(.*?)END-EXEC", re.S)


def _resolve_operand(op: str, values: dict) -> str | None:
    op = op.strip()
    m = re.match(r"^'([^']+)'$", op)
    if m:
        return m.group(1).strip()
    return values.get(op)


def parse_program(path: Path, module: str) -> Program:
    text = path.read_text(errors="replace")
    lines = cobol_code_lines(text)
    body = "\n".join(lines)
    flat = re.sub(r"\s+", " ", body)
    p = Program(name=path.stem.upper(), path=str(path), module=module, loc=len(lines))

    # header comment "Type : BATCH COBOL Program" / "CICS COBOL Program"
    hdr = re.search(r"Type\s*:\s*(.+)", text)
    header_type = hdr.group(1).upper() if hdr else ""

    p.copybooks = sorted(set(re.findall(r"\bCOPY\s+'?([A-Z0-9@#$-]+)'?", flat)))

    # literal VALUEs (constant resolution for CICS DATASET(LIT-X) etc.)
    values = {}
    for m in re.finditer(r"\b\d{2}\s+([A-Z0-9-]+)\s+PIC\s+[^.]*?VALUE\s+'([^']*)'", flat):
        values[m.group(1)] = m.group(2).strip()
    p.literals = {v for v in values.values() if v}

    # static CALLs
    p.calls = sorted(set(re.findall(r"\bCALL\s+'([A-Z0-9@#$-]+)'", flat)))

    # FILE-CONTROL
    for m in re.finditer(
        r"SELECT\s+(?:OPTIONAL\s+)?([A-Z0-9-]+)\s+ASSIGN\s+(?:TO\s+)?([A-Z0-9-]+)(.*?)\.(?=\s|$)",
        flat,
    ):
        clause = m.group(3)
        org = re.search(r"ORGANIZATION\s+(?:IS\s+)?([A-Z]+)", clause)
        acc = re.search(r"ACCESS\s+(?:MODE\s+)?(?:IS\s+)?([A-Z]+)", clause)
        dd = m.group(2)
        dd = dd.split("-")[-1] if dd.startswith(("UT-S-", "S-")) else dd
        p.files[m.group(1)] = FileDecl(
            name=m.group(1), ddname=dd,
            organization=org.group(1) if org else "SEQUENTIAL",
            access=acc.group(1) if acc else "SEQUENTIAL",
        )
    # OPEN modes: OPEN INPUT A B OUTPUT C
    for m in re.finditer(r"\bOPEN\s+((?:(?:INPUT|OUTPUT|I-O|EXTEND)\s+[A-Z0-9-\s]+?)+)(?=\s*(?:\.|END-|IF|MOVE|PERFORM|DISPLAY|EVALUATE|$))", flat):
        mode = None
        for tok in m.group(1).split():
            if tok in ("INPUT", "OUTPUT", "I-O", "EXTEND"):
                mode = tok
            elif mode and tok in p.files:
                p.files[tok].modes.add(mode)

    # CICS
    for m in _CICS_RE.finditer(body):
        cmd = re.sub(r"\s+", " ", m.group(1)).strip()
        verb = cmd.split(" ")[0] if cmd else "?"
        p.cics_cmds[verb] = p.cics_cmds.get(verb, 0) + 1
        for fm in re.finditer(r"\b(?:DATASET|FILE)\s*\(\s*([^)]+?)\s*\)", cmd):
            r = _resolve_operand(fm.group(1), values)
            if r:
                p.cics_files.append(r)
                p.cics_file_ops.append((verb, r))
        for pm in re.finditer(r"\b(?:XCTL|LINK)\b.*?PROGRAM\s*\(\s*([^)]+?)\s*\)", cmd):
            r = _resolve_operand(pm.group(1), values)
            if r:
                p.xctl_literal.append(r)
        for mm in re.finditer(r"\bMAPSET\s*\(\s*([^)]+?)\s*\)", cmd):
            r = _resolve_operand(mm.group(1), values)
            if r:
                p.bms_maps.append(r)
    p.cics_files = sorted(set(p.cics_files))
    p.xctl_literal = sorted(set(p.xctl_literal))
    p.bms_maps = sorted(set(p.bms_maps))

    # SQL tables
    for m in _SQL_RE.finditer(body):
        sql = re.sub(r"\s+", " ", m.group(1))
        p.sql_tables += re.findall(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+([A-Z0-9_.]+)", sql)
    p.sql_tables = sorted({t for t in p.sql_tables if not t.startswith(":") and t not in ("DUAL",)})

    p.uses_ims = bool(re.search(r"CBLTDLI|AIBTDLI|EXEC\s+DLI|\bPCB\b", flat))
    p.uses_mq = any(c.startswith("MQ") for c in p.calls)

    # kind
    if p.cics_cmds or "CICS" in header_type:
        p.kind = "cics"
    elif p.uses_ims:
        p.kind = "ims-batch"
    elif p.sql_tables:
        p.kind = "db2-batch"
    else:
        p.kind = "batch"
    if p.cics_cmds and p.uses_mq:
        p.kind = "cics-mq"

    # metrics
    proc_div = flat.split("PROCEDURE DIVISION", 1)[-1] if "PROCEDURE DIVISION" in flat else ""
    p.metrics = {
        "paragraphs": len(re.findall(r"^\s{0,4}[A-Z0-9][A-Z0-9-]*\s*\.\s*$",
                                     body.split("PROCEDURE DIVISION", 1)[-1], re.M)),
        "if": len(re.findall(r"\bIF\b", proc_div)),
        "evaluate": len(re.findall(r"\bEVALUATE\b", proc_div)),
        "when": len(re.findall(r"\bWHEN\b", proc_div)),
        "perform": len(re.findall(r"\bPERFORM\b", proc_div)),
        "goto": len(re.findall(r"\bGO\s+TO\b", proc_div)),
        "compute": len(re.findall(r"\bCOMPUTE\b", proc_div)),
        "comp3": len(re.findall(r"COMP-3|PACKED-DECIMAL", flat)),
        "redefines": len(re.findall(r"\bREDEFINES\b", flat)),
        "string_ops": len(re.findall(r"\b(?:STRING|UNSTRING|INSPECT)\b", proc_div)),
        "date_ops": len(re.findall(r"FUNCTION\s+(?:CURRENT-DATE|INTEGER-OF-DATE|DATE-OF-INTEGER)|CEEDAYS|CSUTLDTC", flat)),
    }
    # crude cyclomatic estimate
    m_ = p.metrics
    m_["cyclomatic"] = 1 + m_["if"] + m_["when"] + m_["perform"] // 4
    return p


# ---------------------------------------------------------------- JCL
def _jcl_statements(text: str):
    """Yield logical JCL statements (handles continuation) and inline data."""
    stmts, cur = [], None
    in_inline = False
    inline_buf = []
    for raw in text.splitlines():
        line = raw[:72].rstrip()
        if line.startswith("//*"):
            continue
        if in_inline:
            if line.startswith("//") or line.startswith("/*"):
                in_inline = False
                stmts.append("\x00INLINE " + " ".join(inline_buf))
                inline_buf = []
            else:
                inline_buf.append(line.strip())
                continue
        if line.startswith("/*"):
            continue
        if not line.startswith("//"):
            continue
        content = line[2:]
        if cur is not None and (content.startswith(" ") and not re.match(r"^[A-Z0-9#@$.]+\s", content)):
            cur += " " + content.strip()
            continue
        if cur is not None:
            stmts.append(cur)
        cur = content
        if re.search(r"\bDD\s+(\*|DATA)", content):
            in_inline = True
            stmts.append(cur)
            cur = None
    if cur is not None:
        stmts.append(cur)
    if inline_buf:
        stmts.append("\x00INLINE " + " ".join(inline_buf))
    return stmts


def parse_jcl(path: Path, module: str) -> Job:
    text = path.read_text(errors="replace")
    desc_lines = []
    for raw in text.splitlines():
        if raw.startswith("//*"):
            c = raw[3:].strip(" *").strip()
            if c and not re.search(r"Copyright|License|LICENSE|Apache|limitations|WITHOUT|governing|may not use|obtain a copy|http|Unless|BASIS|Rights Reserved|^Ver:", c):
                desc_lines.append(c)
    job = Job(name=path.stem.upper(), path=str(path), module=module,
              description=" ".join(desc_lines)[:300])
    cur = None
    for st in _jcl_statements(text):
        if st.startswith("\x00INLINE "):
            if cur is not None:
                cur.inline_text += " " + st[8:]
            continue
        m = re.match(r"^([A-Z0-9#@$.]*)\s+(JOB|EXEC|DD)\s*(.*)$", st)
        if not m:
            continue
        label, op, rest = m.groups()
        if op == "JOB" and label:
            job.name = label
        elif op == "EXEC":
            pg = re.search(r"\bPGM=([A-Z0-9#@$]+)", rest)
            pr = re.search(r"\bPROC=([A-Z0-9#@$]+)", rest)
            if not pg and not pr:
                first = rest.split(",")[0].strip()
                pr = re.match(r"([A-Z0-9#@$]+)", first)
            parm = re.search(r"\bPARM=(\([^)]*\)|'[^']*'|[^,\s]+)", rest)
            cur = Step(name=label, pgm=pg.group(1) if pg else None,
                       proc=pr.group(1) if pr else None,
                       parm=parm.group(1).strip("'()") if parm else "")
            job.steps.append(cur)
        elif op == "DD" and cur is not None:
            dsn = re.search(r"\bDSN(?:AME)?=([^,\s]+)", rest)
            disp = re.search(r"\bDISP=(\([^)]*\)|[A-Z]+)", rest)
            ddname = label.split(".")[-1] if label else (cur.dds[-1].ddname if cur.dds else "")
            cur.dds.append(DD(
                ddname=ddname,
                dsn=_norm_dsn(dsn.group(1)) if dsn else None,
                disp=(disp.group(1) if disp else "").strip("()").split(",")[0],
                inline=bool(re.match(r"^(\*|DATA)", rest.strip())),
            ))
    return job


def parse_proc_library(paths: list[Path]) -> dict[str, list[DD]]:
    """PROC name -> program it runs (best effort)."""
    procs = {}
    for p in paths:
        text = p.read_text(errors="replace")
        for m in re.finditer(r"^//([A-Z0-9#@$]+)\s+PROC\b(.*?)^//\s+PEND", text, re.M | re.S):
            pg = re.search(r"PGM=([A-Z0-9#@$]+)", m.group(2))
            procs[m.group(1)] = pg.group(1) if pg else None
    return procs


# ---------------------------------------------------------------- CSD
def parse_csd(path: Path) -> dict:
    text = path.read_text(errors="replace")
    files, trans = {}, {}
    for m in re.finditer(r"DEFINE\s+FILE\((\w+)\)(.*?)(?=DEFINE\s|\Z)", text, re.S):
        d = re.search(r"DSNAME\(([^)]+)\)", m.group(2))
        if d:
            files[m.group(1)] = _norm_dsn(d.group(1))
    for m in re.finditer(r"DEFINE\s+TRANSACTION\((\w+)\)(.*?)(?=DEFINE\s|\Z)", text, re.S):
        pg = re.search(r"PROGRAM\((\w+)\)", m.group(2))
        if pg:
            trans[m.group(1)] = pg.group(1)
    return {"files": files, "transactions": trans}


# ---------------------------------------------------------------- scan
def scan(root: Path) -> dict:
    programs, copybooks, jobs, csd = {}, {}, {}, {"files": {}, "transactions": {}}
    proc_files = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or ".git" in f.parts:
            continue
        rel = f.relative_to(root)
        module = rel.parts[0] if len(rel.parts) > 2 and rel.parts[0].startswith("app-") else "core"
        ext = f.suffix.lower()
        parent = f.parent.name.lower()
        if parent in ("proc",) or ext == ".prc":
            proc_files.append(f)
            continue
        if ext in COBOL_EXT and parent in ("cbl", "cob", "src"):
            pr = parse_program(f, module)
            programs[pr.name] = pr
        elif ext in COPY_EXT and parent in ("cpy", "cpy-bms", "copy", "copylib"):
            cb = parse_copybook(f)
            copybooks.setdefault(cb.name, cb)
        elif ext in JCL_EXT and parent == "jcl":
            j = parse_jcl(f, module)
            jobs[f.stem.upper()] = j
        elif ext in CSD_EXT:
            c = parse_csd(f)
            csd["files"].update(c["files"])
            csd["transactions"].update(c["transactions"])
    procs = parse_proc_library(proc_files)
    schedule = []
    for f in root.rglob("*.controlm"):
        schedule += parse_controlm(f)

    # inferred XCTL targets: literal VALUEs in the program or its copybooks that
    # name another known CICS program (menus keep their routing tables in copybooks)
    cics_names = {n for n, p in programs.items() if p.kind.startswith("cics")}
    cb_literals = {}
    for name, cb in copybooks.items():
        txt = "\n".join(cobol_code_lines(Path(cb.path).read_text(errors="replace")))
        cb_literals[name] = set(re.findall(r"'([A-Z0-9]{4,8})\s*'", txt))
    for p in programs.values():
        if not p.kind.startswith("cics"):
            continue
        lits = set(x.strip() for x in p.literals)
        for c in p.copybooks:
            lits |= cb_literals.get(c, set())
        p.xctl_inferred = sorted((lits & cics_names) - {p.name} - set(p.xctl_literal))

    return {"programs": programs, "copybooks": copybooks, "jobs": jobs,
            "csd": csd, "procs": procs, "schedule": schedule}


def parse_controlm(path: Path) -> list[tuple[str, str, str]]:
    """Control-M XML -> (folder, upstream_job, downstream_job) edges via OUTCOND(+)/INCOND."""
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return []
    edges, producers, consumers = [], {}, []
    for folder in tree.getroot():
        fname = folder.get("FOLDER_NAME") or folder.get("JOBNAME") or "?"
        for job in folder.iter("JOB"):
            jn = job.get("JOBNAME")
            for oc in job.findall("OUTCOND"):
                if oc.get("SIGN") == "+":
                    producers[oc.get("NAME")] = jn
            for ic in job.findall("INCOND"):
                consumers.append((fname, ic.get("NAME"), jn))
    for fname, cond, jn in consumers:
        if cond in producers and producers[cond] != jn:
            edges.append((fname, producers[cond], jn))
    return edges
