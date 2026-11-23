"""Generate one Devin playbook (markdown) per migration unit + the structured
output schema each session must return, so results roll up automatically."""
from __future__ import annotations

from pathlib import Path

SESSION_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["unit_id", "status", "files_created", "tests", "equivalence", "open_questions"],
    "properties": {
        "unit_id": {"type": "string"},
        "status": {"type": "string", "enum": ["migrated", "partial", "blocked"]},
        "files_created": {"type": "array", "items": {"type": "string"}},
        "tests": {"type": "object", "properties": {
            "passed": {"type": "integer"}, "failed": {"type": "integer"}}},
        "equivalence": {"type": "object", "properties": {
            "records_compared": {"type": "integer"},
            "mismatches": {"type": "integer"},
            "mismatch_samples": {"type": "array", "items": {"type": "string"}}}},
        "playbook_lessons": {"type": "array", "items": {"type": "string"},
                             "description": "Rules to add to this playbook so the next session avoids the problems hit here"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}


def _short(d: str) -> str:
    return d.replace("AWS.M2.CARDDEMO.", "")


def render_playbook(u, inv, plan) -> str:
    progs = inv["programs"]
    cbs = inv["copybooks"]
    src = lambda path: str(Path(path).relative_to(plan["root"])) if path.startswith(plan["root"]) else path
    L = [f"# {u.id}: {u.title}", ""]
    L += ["## Overview",
          u.description or "(no description in source header)",
          "",
          f"- Wave {u.wave}, size {u.size} ({u.points} pts), estimated {u.sessions} Devin session(s)",
          f"- Target: {u.target}",
          f"- Depends on: {', '.join(u.depends_on) or 'nothing'} (import those modules, never re-implement them)", ""]
    L += ["## What's needed from the user"]
    if u.harness == "ready":
        L += ["- Nothing. Seed data in `app/data/ASCII/` covers every input."]
    elif u.harness == "chained":
        L += [f"- Golden input datasets: {u.harness_detail}"]
    elif u.harness == "replay":
        L += ["- Recorded 3270 screen sessions (input keystrokes + resulting maps) for this transaction, or approval to test against the spec only."]
    else:
        L += [f"- A decision before work starts: {u.harness_detail}"]
    L += [""]
    L += ["## Source inventory"]
    for pn in u.programs:
        p = progs[pn]
        L.append(f"- `{src(p.path)}` ({p.kind}, {p.loc} LOC, cyclomatic~{p.metrics['cyclomatic']})")
    for c in u.copybooks_private:
        L.append(f"- `{src(cbs[c].path)}` (private copybook: translate inside this unit)")
    if u.copybooks_shared:
        L.append(f"- Shared layouts (from F-CODECS, import only): {', '.join(u.copybooks_shared)}")
    if u.jobs:
        L.append(f"- JCL: {', '.join('`app/jcl/' + j + '.jcl`' for j in u.jobs)} (utility steps: {', '.join(u.utilities) or 'none'})")
    L += [""]
    if u.reads or u.writes:
        L += ["## Data contract",
              "| Dataset | Access |", "|---|---|"]
        for d in sorted(set(u.reads) | set(u.writes)):
            acc = ("R" if d in u.reads else "") + ("W" if d in u.writes else "")
            L.append(f"| `{_short(d)}` | {acc} |")
        if u.intermediate:
            L.append(f"\nIntermediates created and consumed inside the job: {', '.join(_short(d) for d in u.intermediate)}. "
                     "Keep them as in-memory or temp files; don't persist them as new tables.")
        L += [""]
    L += ["## Procedure"]
    if u.kind == "foundation" and u.id == "F-CODECS":
        L += ["1. For every copybook listed, generate a frozen dataclass plus `decode(bytes|str) -> obj` and `encode(obj) -> str` for the fixed-width record.",
              "2. PIC 9/S9 with V → `decimal.Decimal` with exact scale. COMP-3 → packed-decimal codec. Never use float.",
              "3. REDEFINES → explicit variant views over the same raw bytes; record which view each consumer uses.",
              "4. Round-trip test every seed file in `app/data/ASCII/`: `encode(decode(line)) == line` for 100% of records.",
              "5. Publish as package `carddemo_codecs` with a version; every other unit pins it."]
    elif u.kind == "batch" or (u.kind == "extension" and u.jobs):
        L += ["1. Read the JCL first: list each step, its program or utility, and its DD → dataset bindings. Reproduce utility steps (SORT/IDCAMS/IEBGENER) in Python with the same keys and record order.",
              "2. Translate the PROCEDURE DIVISION paragraph by paragraph into functions with the same names (snake_case). Keep a comment mapping each function to its source line range.",
              "3. Use `carddemo_codecs` for every record read or written. VSAM KSDS → keyed store interface (dict in tests; DynamoDB adapter in deploy).",
              "4. Preserve FILE STATUS semantics: map each status code the program checks to an explicit exception or return value.",
              "5. Run the equivalence harness: same inputs → legacy (GnuCOBOL) output vs. new output, compared record by record and field by field.",
              "6. Iterate until mismatches = 0. For each mismatch class you fix, add a rule to `playbook_lessons`."]
    elif u.kind in ("online", "extension"):
        L += ["1. Map the BMS mapset to a typed request/response model (fields, lengths, attributes, PF-key actions).",
              "2. Replace pseudo-conversational RETURN TRANSID / COMMAREA with the session model from F-ONLINE-PLATFORM.",
              "3. Translate each paragraph to a handler function; XCTL → route to the target screen's handler.",
              "4. File access goes only through the F-ONLINE-PLATFORM repository layer (READ/STARTBR/READNEXT → keyed queries, REWRITE → optimistic update).",
              "5. Replay recorded screen flows and diff every output field. Error messages must match the legacy text exactly."]
    else:
        L += ["1. Migrate as a library with an identical call contract (LINKAGE SECTION → typed signature).",
              "2. Port every caller's call site test vector into unit tests."]
    L += ["", "## Specifications",
          "- Output must be byte-identical to legacy for every compared record (after the agreed EBCDIC→ASCII normalisation).",
          "- Decimal arithmetic must match COBOL rounding/truncation (ROUNDED vs. default truncation) exactly.",
          "- Return structured output matching the session schema, including equivalence counts."]
    if u.risks:
        L += ["", "## Advice (risks detected by Scout)"] + [f"- {r}" for r in u.risks]
    L += ["", "## Forbidden actions",
          "- Do not modify legacy sources under `app/` (they are the oracle).",
          "- Do not re-implement shared codecs or subprograms; import them.",
          "- Do not mark the unit migrated while any equivalence mismatch remains unexplained.",
          "- Do not use binary floating point for monetary fields."]
    return "\n".join(L) + "\n"
