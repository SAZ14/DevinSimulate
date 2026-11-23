"""scout: scope a mainframe estate for parallel Devin migration.

  python -m scout.cli plan <app_root> -o out/          # inventory, units, playbooks, report
  python -m scout.cli dispatch out/ --wave 0           # dry-run Devin API requests
  python -m scout.cli dispatch out/ --wave 0 --execute --repo org/carddemo-migration
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
from pathlib import Path

from .analyze import analyze
from .devin import dispatch
from .parse import scan
from .playbooks import SESSION_OUTPUT_SCHEMA, render_playbook


def _ser(o):
    if dataclasses.is_dataclass(o):
        return {k: _ser(v) for k, v in dataclasses.asdict(o).items()}
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, dict):
        return {k: _ser(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_ser(v) for v in o]
    return o


def plan(root: Path, out: Path, app_name: str):
    inv = scan(root)
    a = analyze(inv, root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "playbooks").mkdir(exist_ok=True)
    ctx = {"root": str(root.parent) + "/"}
    books = {}
    for u in a["units"]:
        md = render_playbook(u, inv, ctx)
        books[u.id] = md
        (out / "playbooks" / f"{u.id}.md").write_text(md)
    units = [_ser(u) for u in a["units"]]
    planj = {"app": app_name, "source": str(root), "generated": dt.date.today().isoformat(),
             "totals": a["totals"], "units": units, "cutover": a["cutover"],
             "datasets": a["datasets"], "data_edges": a["data_edges"],
             "schedule_edges": a["schedule_edges"], "program_edges": a["program_edges"],
             "shared_copybooks": a["shared_copybooks"], "seeds": a["seeds"]}
    (out / "plan.json").write_text(json.dumps(planj, indent=2))
    (out / "inventory.json").write_text(json.dumps(_ser({
        "programs": inv["programs"], "copybooks": inv["copybooks"], "jobs": inv["jobs"],
        "csd": inv["csd"]}), indent=1, default=str))
    (out / "session_output_schema.json").write_text(json.dumps(SESSION_OUTPUT_SCHEMA, indent=2))
    tpl = (Path(__file__).parent / "report_template.html").read_text()
    data = json.dumps({**planj, "playbooks": books}).replace("</", "<\\/")
    (out / "report.html").write_text(tpl.replace("__DATA__", data))
    t = a["totals"]
    print(f"{t['programs']} programs / {t['loc']} LOC -> {t['units']} units, "
          f"{t['sessions']} est. sessions, max {t['parallel_max']} parallel, {t['harness_ready']} harness-ready")
    print(f"wrote {out}/plan.json, report.html, playbooks/*.md")


def main():
    ap = argparse.ArgumentParser(prog="scout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("root", type=Path); p.add_argument("-o", "--out", type=Path, default=Path("out"))
    p.add_argument("--name", default="CardDemo")
    d = sub.add_parser("dispatch"); d.add_argument("plan_dir", type=Path); d.add_argument("--wave", type=int, required=True)
    d.add_argument("--execute", action="store_true"); d.add_argument("--repo"); d.add_argument("--max-acu", type=int)
    d.add_argument("--only", nargs="*"); d.add_argument("--poll", action="store_true")
    args = ap.parse_args()
    if args.cmd == "plan":
        plan(args.root.resolve(), args.out, args.name)
    else:
        dispatch(args.plan_dir, args.wave, args.execute, args.repo, args.max_acu, args.only, args.poll)


if __name__ == "__main__":
    main()
