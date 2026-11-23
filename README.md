# Scout

Scout plans parallel Devin migrations of mainframe code. You point it at a COBOL estate and get back:

- **Inventory**: programs, copybooks, JCL jobs, CICS file definitions and the Control-M schedule. For each program it records the kind (batch, CICS, DB2, IMS, MQ), complexity metrics, file I/O with open modes, CICS file operations, XCTL targets and SQL tables.
- **Migration units**: one per JCL job (batch) and one per CICS screen (online). Shared copybooks and subprograms are pulled out into Wave 0, which is what makes the rest parallel-safe.
- **Harness readiness**: for each unit, whether a test oracle exists right now (inputs covered by seed data), whether it needs a golden run of upstream jobs, or whether it's blocked.
- **Cutover groups**: strongly-connected components of the batch data-flow graph, meaning jobs that update the same master files and must switch systems together.
- **One Devin playbook per unit**, plus a structured-output schema so every session reports its equivalence results the same way.
- **Dispatcher**: Devin API v3, wave by wave and dependency-aware. It's a dry run unless you pass `--execute`.

## Run

```bash
git clone https://github.com/aws-samples/aws-mainframe-modernization-carddemo src/carddemo
python -m scout.cli plan src/carddemo/app -o out          # out/report.html, plan.json, playbooks/
python -m scout.cli dispatch out --wave 0                 # prints the exact API requests
DEVIN_API_KEY=cog_... DEVIN_ORG_ID=org-... \
  python -m scout.cli dispatch out --wave 0 --execute --repo you/carddemo-migration --poll
```

There are no dependencies beyond the Python 3.10+ standard library.

## CardDemo result

| | |
|---|---|
| Source | 44 programs, 22,904 LOC, 62 copybooks, 46 JCL jobs |
| Units | 45 (3 foundation, 12 batch, 17 online, 13 extension) |
| Max parallel | 17 sessions (wave 2) |
| Verifiable today | 8 units (seed data covers all inputs) |
| Cutover group | POSTTRAN + INTCALC (both update `ACCTDATA`) |

## Known limits

- Parsing uses regex, not a COBOL grammar. It's tested on CardDemo only.
- Inferred XCTL targets (menu tables in copybooks) are labelled `XCTL?`.
- Effort coefficients in `analyze.py:W` are heuristics. Recalibrate them using ACUs and review time from wave 1.
- The GET-session path in `devin.py` is inferred from the docs slug. Check it against `/v3-openapi.yaml`.
