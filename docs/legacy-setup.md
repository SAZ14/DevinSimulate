# Legacy setup: running CardDemo batch with GnuCOBOL

Running log of every decision and workaround needed to run the legacy COBOL batch
programs locally. Each entry says **what**, **why**, and **effect on fidelity**.

## Environment

| Item | Value |
|---|---|
| OS | Ubuntu (Linux x86-64, container) |
| Compiler | GnuCOBOL 3.1.2.0 (`apt-get install gnucobol3`) |
| Indexed file handler | Berkeley DB (BDB), as built into the Ubuntu package |
| Legacy source | `aws-samples/aws-mainframe-modernization-carddemo` @ `59cc6c2fd7ebd7ef7925cad552a01a4b8b6e4d5e` (2025-10-16) |

## Workarounds and decisions

### W1. `legacy/` vendored from upstream CardDemo
- **What:** At the start the repo was empty. The uploaded `scout.zip` held `scout/`, its
  README and a generated `out/`, but no COBOL. Scout's README names upstream CardDemo
  as the source, so `app/` from upstream (plus `LICENSE`, `NOTICE`) was copied
  verbatim into `legacy/app` at the commit above.
- **Why:** The programs under test must be the ones Scout planned against.
- **Verification:** `python -m scout.cli plan legacy/app` reproduces `out/plan.json`
  exactly: identical totals (44 programs, 62 copybooks, 46 jobs, 22,904 LOC, 45 units),
  identical unit list, and byte-identical playbooks.
- **Fidelity:** None lost. `legacy/` is read-only from here on.

### W2. Compile dialect: `-std=ibm`
- **What:** Programs are compiled with `cobc -x -std=ibm -I legacy/app/cpy`.
- **Why:** CardDemo targets IBM Enterprise COBOL. CBACT01C also compiles cleanly under
  the default dialect, but `-std=ibm` matches the source's dialect for
  reserved words and numeric-truncation behaviour (`TRUNC(STD)`-like binary handling).
  That matters for byte-identical golden output.
- **Fidelity:** Closer to mainframe semantics than the default dialect.

### W3. `_FORTIFY_SOURCE redefined` warning (ignored)
- **What:** Every `cobc` run prints `<command-line>: warning: "_FORTIFY_SOURCE" redefined`.
- **Why:** Ubuntu's GnuCOBOL package bakes `-D_FORTIFY_SOURCE=2` into `COB_CFLAGS`,
  and the system GCC already defines it. This is a C-level warning from the packaging,
  not a COBOL diagnostic.
- **Fidelity:** No effect. The runner filters this line out of compile logs.

## Per-program compile status

| Program | Job | Compiles | External CALLs needing stubs |
|---|---|---|---|
| CBACT01C | READACCT | yes, rc=0, no COBOL diagnostics | `COBDATFT` (assembler, `legacy/app/asm/COBDATFT.asm`), `CEE3ABD` (LE abend) |
