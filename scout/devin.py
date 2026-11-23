"""Dispatch migration units to Devin (API v3), wave by wave.

Dry-run by default: prints the exact request bodies. With --execute it needs
DEVIN_API_KEY (service-user `cog_...` token or PAT) and DEVIN_ORG_ID (`org-...`).

Endpoints used (docs.devin.ai, v3):
  POST /v3/organizations/{org_id}/sessions   create session
  GET  /v3/organizations/{org_id}/sessions/{session_id}   poll (path per docs slug
       get-organizations-session; verify against /v3-openapi.yaml before production use)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from .playbooks import SESSION_OUTPUT_SCHEMA

BASE = "https://api.devin.ai/v3"


def build_request(unit: dict, playbook_md: str, repo: str | None, max_acu: int | None) -> dict:
    body = {
        "title": f"[Scout] {unit['id']}: {unit['title']}",
        "prompt": (f"You are migrating one unit of the CardDemo mainframe application.\n"
                   f"Follow this playbook exactly and return the structured output when done.\n\n{playbook_md}"),
        "tags": ["scout", f"wave-{unit['wave']}", unit["kind"], unit["id"]],
        "structured_output_required": True,
        "structured_output_schema": SESSION_OUTPUT_SCHEMA,
    }
    if repo:
        body["repos"] = [repo]
    if max_acu:
        body["max_acu_limit"] = max_acu
    return body


def _call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['DEVIN_API_KEY']}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def dispatch(plan_dir: Path, wave: int, execute: bool, repo: str | None,
             max_acu: int | None, only: list[str] | None = None, poll: bool = False):
    plan = json.loads((plan_dir / "plan.json").read_text())
    state_p = plan_dir / "sessions.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    done = {k for k, v in state.items() if v.get("status") == "exit"
            and (v.get("structured_output") or {}).get("status") == "migrated"}

    todo = [u for u in plan["units"] if u["wave"] == wave and (not only or u["id"] in only)]
    org = os.environ.get("DEVIN_ORG_ID", "org-XXXX")
    for u in todo:
        if u["id"] in state:
            print(f"skip {u['id']}: already dispatched ({state[u['id']].get('url')})")
            continue
        unmet = [d for d in u["depends_on"] if d not in done]
        if unmet and execute:
            print(f"hold {u['id']}: waiting on {', '.join(unmet)}")
            continue
        if u["harness"] == "blocked":
            print(f"hold {u['id']}: harness blocked -- {u['harness_detail']}")
            continue
        md = (plan_dir / "playbooks" / f"{u['id']}.md").read_text()
        body = build_request(u, md, repo, max_acu)
        if not execute:
            print(f"--- DRY RUN  POST /v3/organizations/{org}/sessions  ({u['id']})")
            print(json.dumps({**body, "prompt": body["prompt"][:200] + " ...", "structured_output_schema": "<session_output_schema.json>"}, indent=2))
            continue
        resp = _call("POST", f"/organizations/{org}/sessions", body)
        state[u["id"]] = {"session_id": resp["session_id"], "url": resp["url"], "status": resp["status"]}
        print(f"launched {u['id']} -> {resp['url']}")
        state_p.write_text(json.dumps(state, indent=2))

    if execute and poll:
        while True:
            live = [k for k, v in state.items() if v["status"] not in ("exit", "error", "suspended")]
            if not live:
                break
            for k in live:
                s = _call("GET", f"/organizations/{org}/sessions/{state[k]['session_id']}")
                state[k].update(status=s["status"], acus=s.get("acus_consumed"),
                                prs=s.get("pull_requests"), structured_output=s.get("structured_output"))
            state_p.write_text(json.dumps(state, indent=2))
            time.sleep(30)
        summarize(state)


def summarize(state: dict):
    rows = []
    for k, v in sorted(state.items()):
        so = v.get("structured_output") or {}
        eq = so.get("equivalence") or {}
        rows.append((k, v.get("status"), so.get("status"), eq.get("records_compared"), eq.get("mismatches"), v.get("acus")))
    print(f"{'unit':28} {'session':9} {'result':9} {'records':>8} {'mism':>5} {'ACUs':>6}")
    for r in rows:
        print(f"{r[0]:28} {str(r[1]):9} {str(r[2]):9} {str(r[3]):>8} {str(r[4]):>5} {str(r[5]):>6}")
