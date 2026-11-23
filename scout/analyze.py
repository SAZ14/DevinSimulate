"""Dependency graph, migration-unit decomposition, wave planning, scoring.

Core idea: with golden-master datasets captured from the legacy system, every
batch job can be migrated AND verified independently -> they parallelize.
What actually serializes the work is (a) shared record layouts (copybooks)
every unit must decode identically, and (b) shared subprograms. Those become
Wave 0. Data-flow order only matters for cutover, so it's computed separately.
"""
from __future__ import annotations

import difflib
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .parse import UTILITY_PGMS

# Effort coefficients -- heuristic, tune per engagement after the first wave.
W = {
    "per_100_loc": 1.0, "per_20_cyclo": 1.0, "goto": 0.4, "comp3": 0.3,
    "redefines": 0.3, "file": 0.6, "cics_cmd": 0.08, "sql_table": 1.5,
    "ims": 6.0, "mq": 4.0, "string_op": 0.08, "date_op": 0.3,
    "points_per_session": 6.0, "review_hours_per_point": 0.35,
}

WRITE_MODES = {"OUTPUT", "EXTEND", "I-O"}
READ_MODES = {"INPUT", "I-O"}
CICS_WRITE = {"WRITE", "REWRITE", "DELETE"}
CICS_READ = {"READ", "STARTBR", "READNEXT", "READPREV"}


@dataclass
class Unit:
    id: str
    title: str
    kind: str            # foundation | batch | online | infra | extension
    wave: int
    programs: list = field(default_factory=list)
    jobs: list = field(default_factory=list)
    copybooks_private: list = field(default_factory=list)
    copybooks_shared: list = field(default_factory=list)
    reads: list = field(default_factory=list)
    writes: list = field(default_factory=list)
    utilities: list = field(default_factory=list)
    depends_on: list = field(default_factory=list)
    points: float = 0.0
    size: str = "S"
    sessions: int = 1
    review_hours: float = 0.0
    risks: list = field(default_factory=list)
    harness: str = "n/a"         # ready | chained | blocked | n/a
    harness_detail: str = ""
    intermediate: list = field(default_factory=list)
    target: str = ""
    description: str = ""
    loc: int = 0


def _size(points: float) -> str:
    return "S" if points < 4 else "M" if points < 9 else "L" if points < 16 else "XL"


def program_points(p) -> tuple[float, list]:
    m = p.metrics
    risks = []
    pts = (p.loc / 100 * W["per_100_loc"] + m["cyclomatic"] / 20 * W["per_20_cyclo"]
           + m["goto"] * W["goto"] + m["comp3"] * W["comp3"] + m["redefines"] * W["redefines"]
           + len(p.files) * W["file"] + sum(p.cics_cmds.values()) * W["cics_cmd"]
           + len(p.sql_tables) * W["sql_table"] + (W["ims"] if p.uses_ims else 0)
           + (W["mq"] if p.uses_mq else 0) + m["string_ops"] * W["string_op"]
           + m["date_ops"] * W["date_op"])
    if m["goto"] >= 5:
        risks.append(f"{m['goto']} GO TOs: unstructured control flow, translate paragraph-by-paragraph")
    if m["comp3"]:
        risks.append(f"{m['comp3']} COMP-3 fields: use Decimal, never float")
    if m["redefines"] >= 2:
        risks.append(f"{m['redefines']} REDEFINES: overlapping storage, needs explicit union decoding")
    if p.uses_ims:
        risks.append("IMS DL/I calls: hierarchical DB needs a target data-model decision")
    if p.uses_mq:
        risks.append("MQ messaging: pick SQS/MSK equivalent and preserve message semantics")
    if p.sql_tables:
        risks.append(f"DB2 SQL on {', '.join(p.sql_tables)}: map to RDS/Aurora, check isolation levels")
    if "CEE3ABD" in p.calls:
        risks.append("LE abend (CEE3ABD): map to explicit error exit codes")
    if m["date_ops"]:
        risks.append("date arithmetic: check century/Lilian-day semantics")
    if any(f.organization == "INDEXED" and (f.modes & {"I-O"}) for f in p.files.values()):
        risks.append("in-place VSAM KSDS updates (I-O): needs keyed store with read-modify-write")
    return pts, risks


def _ascii_match(dsn: str, seeds: list[Path]) -> Path | None:
    quals = [q for q in dsn.split(".") if q not in ("AWS", "M2", "CARDDEMO", "VSAM", "KSDS", "PS", "AIX", "PATH")]
    if not quals:
        return None
    tok = quals[0].lower()
    best, score = None, 0.0
    for s in seeds:
        st = s.stem.lower()
        r = 1.0 if st == tok else difflib.SequenceMatcher(None, st, tok).ratio()
        if r > score:
            best, score = s, r
    return best if score >= 0.85 else None


def analyze(inv: dict, root: Path) -> dict:
    progs, cbs, jobs, csd = inv["programs"], inv["copybooks"], inv["jobs"], inv["csd"]
    procs = inv["procs"]

    # ---- copybook closure per program
    def cb_closure(names):
        seen, stack = set(), list(names)
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if n in cbs:
                stack += cbs[n].nested_copies
        return seen

    def step_pgm(s):
        pg = s.pgm or (procs.get(s.proc) if s.proc else None)
        if pg in ("DFSRRC00", "IKJEFT01", "IKJEFT1B"):
            blob = (s.parm + " " + s.inline_text).upper()
            for name in re.findall(r"[A-Z0-9#@$]{4,8}", blob):
                if name in progs:
                    return name
        return pg

    # ---- call graph
    callers = defaultdict(set)
    for p in progs.values():
        for c in p.calls:
            if c in progs:
                callers[c].add(p.name)

    # ---- dataset IO per program (batch via JCL DDs, online via CSD)
    def batch_io(p, step):
        reads, writes = set(), set()
        dd = {d.ddname: d for d in step.dds}
        for f in p.files.values():
            d = dd.get(f.ddname)
            if not d or not d.dsn:
                continue
            modes = f.modes or ({"OUTPUT"} if d.disp in ("NEW", "MOD") else {"INPUT"})
            if modes & READ_MODES:
                reads.add(d.dsn)
            if modes & WRITE_MODES:
                writes.add(d.dsn)
        return reads, writes

    def online_io(p):
        reads, writes = set(), set()
        for verb, f in p.cics_file_ops:
            dsn = csd["files"].get(f, f"CICS:{f}")
            if verb in CICS_WRITE:
                writes.add(dsn)
            if verb in CICS_READ:
                reads.add(dsn)
        return reads, writes

    # ---- copybook usage counts (to find shared contracts)
    cb_users = defaultdict(set)
    for p in progs.values():
        for c in cb_closure(p.copybooks):
            cb_users[c].add(p.name)
    bms_cbs = {n for n, cb in cbs.items() if "cpy-bms" in cb.path}

    units: list[Unit] = []
    assigned = set()
    seeds = sorted((root / "data" / "ASCII").glob("*.txt")) if (root / "data" / "ASCII").exists() else []

    # ---- batch units: one per JCL job that runs COBOL
    dsn_writers = defaultdict(set)
    for jn, j in sorted(jobs.items()):
        cobol_steps = []
        for s in j.steps:
            pg = step_pgm(s)
            if pg in progs and not progs[pg].kind.startswith("cics"):
                cobol_steps.append((s, progs[pg]))
        utils = sorted({(step_pgm(s) or s.proc or "?") for s in j.steps
                        if step_pgm(s) not in progs})
        if not cobol_steps:
            continue
        u = Unit(id=f"B-{jn}", title=f"Batch job {jn}", kind="batch", wave=1, jobs=[jn],
                 description=j.description, utilities=utils,
                 target="Python 3.12 batch handler (AWS Lambda / Step Functions), records via shared codecs")
        reads, writes, intermediate = set(), set(), set()
        cobol_by_step = {id(s): p for s, p in cobol_steps}
        # walk steps in order: a dataset created earlier in the same job and read
        # later is an intermediate, not an external input
        for s in j.steps:
            p = cobol_by_step.get(id(s))
            if p is not None:
                u.programs.append(p.name)
                r, w = batch_io(p, s)
                for c in p.calls:
                    if c in progs and len(callers[c]) == 1 and c not in u.programs:
                        u.programs.append(c)
            else:
                r, w = set(), set()
                for d in s.dds:
                    if not d.dsn or d.dsn.endswith(("LOADLIB", "LOAD", "SDFSRESL", "PROCLIB", "PSBLIB", "DBDLIB")) or d.dsn == "NULLFILE":
                        continue
                    if d.ddname in ("STEPLIB", "SYSIN", "SYSPRINT", "SYSOUT", "SYSTSIN", "DFSRESLB"):
                        continue
                    if d.disp in ("NEW", "MOD") or d.ddname in ("SORTOUT", "FILEOUT", "OUTFILE", "SYSUT2"):
                        w.add(d.dsn)
                    else:
                        r.add(d.dsn)
            for d in r:
                if d in writes and d not in reads:
                    intermediate.add(d)
                else:
                    reads.add(d)
            writes |= w
        u.reads, u.writes = sorted(reads), sorted(writes)
        u.intermediate = sorted(intermediate)
        for w in writes:
            dsn_writers[w].add(u.id)
        units.append(u)
        assigned |= set(u.programs)

    # ---- online units: one per CICS program
    for n, p in sorted(progs.items()):
        if not p.kind.startswith("cics") or n in assigned:
            continue
        r, w = online_io(p)
        u = Unit(id=f"O-{n}", title=f"Online screen {n}", kind="online", wave=2,
                 programs=[n], reads=sorted(r), writes=sorted(w),
                 target="Python service (FastAPI on Lambda) + React form replacing BMS map",
                 description=_header_function(p.path))
        if p.module != "core":
            u.kind, u.wave = "extension", 3
            u.title = f"Extension screen {n} ({p.module})"
        units.append(u)
        assigned.add(n)

    # ---- leftover programs: shared subprograms -> foundation; orphans -> batch-ish
    shared_subs = [n for n in progs if n not in assigned and callers.get(n)]
    orphans = [n for n in progs if n not in assigned and not callers.get(n)]
    for n in sorted(orphans):
        p = progs[n]
        r = sorted(set())
        u = Unit(id=f"B-{n}", title=f"Unscheduled program {n}", kind="batch", wave=1,
                 programs=[n], description=_header_function(p.path) + " (no JCL found -- confirm it is still in use)",
                 target="Python 3.12 batch handler")
        if p.module != "core":
            u.kind, u.wave = "extension", 3
        units.append(u)
        assigned.add(n)

    # ---- extension modules: their batch units also go to wave 3
    for u in units:
        if any(progs[p].module != "core" for p in u.programs if p in progs):
            u.kind, u.wave = "extension", 3

    # ---- shared copybooks = used by >= 2 units (record contracts)
    unit_cbs = {}
    for u in units:
        s = set()
        for pn in u.programs:
            s |= cb_closure(progs[pn].copybooks)
        unit_cbs[u.id] = s
    cb_unit_count = defaultdict(int)
    for s in unit_cbs.values():
        for c in s:
            cb_unit_count[c] += 1
    shared_cbs = sorted(c for c, k in cb_unit_count.items() if k >= 2 and c in cbs and c not in bms_cbs)
    for u in units:
        u.copybooks_shared = sorted(c for c in unit_cbs[u.id] if c in shared_cbs)
        u.copybooks_private = sorted(c for c in unit_cbs[u.id] if c not in shared_cbs and c in cbs)

    found = []
    if shared_cbs:
        cpts = sum(1 + cbs[c].fields / 25 + cbs[c].comp3 * 0.3 + cbs[c].redefines * 0.5 for c in shared_cbs) / 3
        risks = []
        if any(cbs[c].comp3 for c in shared_cbs):
            risks.append("packed decimal (COMP-3) fields in shared layouts")
        if any(cbs[c].redefines for c in shared_cbs):
            risks.append("REDEFINES in shared layouts -- every unit must decode identically")
        found.append(Unit(
            id="F-CODECS", title="Shared record-layout codecs", kind="foundation", wave=0,
            copybooks_shared=shared_cbs, points=round(cpts, 1), risks=risks,
            target="Python package: fixed-width record dataclasses + encode/decode (Decimal-safe), round-trip tested on seed data",
            description=f"Translate the {len(shared_cbs)} copybooks shared across units into one codec library. Every other unit imports it; this is the contract that makes parallel migration safe.",
            harness="ready" if seeds else "blocked",
            harness_detail=f"round-trip encode(decode(record)) == record on {len(seeds)} seed files" if seeds else "no seed data"))
    if shared_subs:
        sub_progs = sorted(shared_subs)
        pts = sum(program_points(progs[n])[0] for n in sub_progs)
        found.append(Unit(
            id="F-SUBPROGS", title="Shared subprograms", kind="foundation", wave=0,
            programs=sub_progs, points=round(pts, 1),
            description="Called by multiple programs; migrate once as a library before dependents.",
            target="Python module with identical call contract (LINKAGE SECTION -> typed function signature)",
            risks=[r for n in sub_progs for r in program_points(progs[n])[1]]))
    online = [u for u in units if u.kind == "online"]
    if online:
        found.append(Unit(
            id="F-ONLINE-PLATFORM", title="Online platform: COMMAREA session + CICS file access layer",
            kind="foundation", wave=0, points=5.0,
            copybooks_shared=[c for c in ("COCOM01Y",) if c in cbs],
            target="Session-state model replacing COMMAREA pseudo-conversation; repository layer over the keyed stores",
            description="CICS screens hand state through COMMAREA and XCTL. Define the session model and data-access layer once so each screen migrates independently.",
            risks=["pseudo-conversational flow (RETURN TRANSID) must become explicit request/session state"]))
    for f in found:
        f.size = _size(f.points)
        f.sessions = max(1, math.ceil(f.points / W["points_per_session"]))
        f.review_hours = round(f.points * W["review_hours_per_point"], 1)

    # ---- score units
    for u in units:
        pts, risks = 0.0, []
        for pn in u.programs:
            a, b = program_points(progs[pn])
            pts += a
            risks += b
            u.loc += progs[pn].loc
        pts += 0.3 * len(u.utilities)
        u.points = round(pts, 1)
        u.risks = sorted(set(risks))
        u.size = _size(u.points)
        u.sessions = max(1, math.ceil(u.points / W["points_per_session"]))
        u.review_hours = round(u.points * W["review_hours_per_point"], 1)
        deps = []
        if u.copybooks_shared and "F-CODECS" in {f.id for f in found}:
            deps.append("F-CODECS")
        if any(c in shared_subs for pn in u.programs for c in progs[pn].calls):
            deps.append("F-SUBPROGS")
        if u.kind in ("online",) or (u.kind == "extension" and any(progs[p].kind.startswith("cics") for p in u.programs)):
            deps.append("F-ONLINE-PLATFORM")
        u.depends_on = deps
        if any("WRITEQ" in progs[pn].cics_cmds for pn in u.programs):
            u.risks.append("writes a TD queue (likely submits batch JCL via internal reader) -- becomes a job trigger")
        if u.loc > 2000:
            u.risks.append(f"{u.loc} LOC in one program: split into sub-sessions by paragraph section")
        if "SORT" in u.utilities:
            u.risks.append("DFSORT step: reproduce sort keys/collation exactly (EBCDIC vs ASCII order)")
        if any("GDG" in r or "(+1)" in r for r in u.reads):
            pass

    # ---- harness readiness (batch): are inputs seeded or produced upstream?
    seeded = {}
    for u in units:
        for d in u.reads + u.writes:
            if d not in seeded:
                seeded[d] = _ascii_match(d, seeds)
    for u in units:
        if u.kind not in ("batch",):
            if u.kind == "online":
                u.harness = "replay"
                u.harness_detail = "screen-flow replay: record 3270 inputs/outputs per transaction, diff field-by-field"
            elif u.kind == "extension":
                u.harness = "blocked"
                u.harness_detail = "needs IMS/DB2/MQ emulation or a target-platform decision first"
            continue
        if not u.reads and u.jobs and not any(progs[pn].files for pn in u.programs):
            u.harness = "ready"
            u.harness_detail = "no file I/O: compare SYSOUT and return code"
            continue
        if not u.reads:
            u.harness = "blocked"
            u.harness_detail = ("no JCL binds this program's files -- find its job or confirm it is dead code"
                                if not u.jobs else "no file inputs detected -- confirm inputs (PARM/SYSIN)")
            continue
        fixtures = [d for d in u.reads if not seeded.get(d) and re.search(r"PARM|DATE|CNTL|CTL", d.split(".")[-1])]
        missing = [d for d in u.reads if not seeded.get(d) and d not in fixtures]
        produced = [d for d in missing if dsn_writers.get(d, set()) - {u.id}] if missing else []
        fx = (" + hand-written fixture for " + ", ".join(d.split(".")[-1] for d in fixtures)) if fixtures else ""
        if not missing:
            u.harness = "ready"
            u.harness_detail = "inputs seeded: " + ", ".join(seeded[d].name for d in u.reads if seeded.get(d)) + fx
        elif len(produced) == len(missing):
            ups = sorted({w for d in produced for w in dsn_writers[d] - {u.id}})
            u.harness = "chained"
            u.harness_detail = "inputs produced by " + ", ".join(ups) + " -- run legacy upstream on GnuCOBOL to capture golden inputs"
        else:
            un = [d for d in missing if d not in produced]
            u.harness = "blocked"
            u.harness_detail = "no seed or upstream producer for: " + ", ".join(un)

    all_units = found + units

    # ---- cutover order: data flow between batch units (writer -> reader) + scheduler edges
    edges = set()
    for u in units:
        for d in u.reads:
            for w in dsn_writers.get(d, ()):
                if w != u.id:
                    edges.add((w, u.id, d))
    sched = [(f, f"B-{a}", f"B-{b}") for f, a, b in inv.get("schedule", [])]

    # ---- program-level graph for the report
    pgraph = []
    for p in progs.values():
        for c in p.calls:
            if c in progs:
                pgraph.append((p.name, c, "CALL"))
        for c in p.xctl_literal:
            if c in progs:
                pgraph.append((p.name, c, "XCTL"))
        for c in p.xctl_inferred:
            pgraph.append((p.name, c, "XCTL?"))

    ds = defaultdict(lambda: {"readers": set(), "writers": set()})
    for u in all_units:
        for d in u.reads:
            ds[d]["readers"].add(u.id)
        for d in u.writes:
            ds[d]["writers"].add(u.id)
    datasets = {d: {"readers": sorted(v["readers"]), "writers": sorted(v["writers"]),
                    "seed": (seeded.get(d).name if seeded.get(d) else None)} for d, v in sorted(ds.items())}
    cutover = _cutover_groups([u.id for u in units if u.kind == "batch"], edges)

    totals = {
        "programs": len(progs), "copybooks": len(cbs), "jobs": len(jobs),
        "loc": sum(p.loc for p in progs.values()),
        "units": len(all_units),
        "parallel_max": max(sum(1 for u in all_units if u.wave == w) for w in range(4)),
        "sessions": sum(u.sessions for u in all_units),
        "review_hours": round(sum(u.review_hours for u in all_units), 1),
        "harness_ready": sum(1 for u in all_units if u.harness == "ready"),
        "by_kind": {k: sum(1 for p in progs.values() if p.kind == k) for k in sorted({p.kind for p in progs.values()})},
        "utility_only_jobs": sorted(jn for jn in jobs if not any(f"B-{jn}" == u.id for u in units)),
    }
    return {"units": all_units, "data_edges": sorted(edges), "cutover": cutover, "datasets": datasets, "schedule_edges": sched,
            "program_edges": pgraph, "totals": totals, "seeds": [s.name for s in seeds],
            "shared_copybooks": shared_cbs}


def _header_function(path: str) -> str:
    try:
        txt = Path(path).read_text(errors="replace")
    except OSError:
        return ""
    m = re.search(r"Function\s*:\s*(.+)", txt)
    return m.group(1).strip() if m else ""


def _cutover_groups(nodes, edges):
    """Tarjan SCC + topological order. A cycle = jobs that must cut over together
    (they read each other's outputs, e.g. a master file updated by several jobs)."""
    adj = defaultdict(set)
    for a, b, _ in edges:
        if a in nodes and b in nodes:
            adj[a].add(b)
    index, low, stack, on, sccs, i = {}, {}, [], set(), [], [0]

    def strong(v):
        index[v] = low[v] = i[0]; i[0] += 1
        stack.append(v); on.add(v)
        for w in adj[v]:
            if w not in index:
                strong(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop(); on.discard(w); comp.append(w)
                if w == v:
                    break
            sccs.append(sorted(comp))
    for n in nodes:
        if n not in index:
            strong(n)
    return list(reversed(sccs))   # Tarjan emits reverse-topological
