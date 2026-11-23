# Legacy setup: running CardDemo batch with GnuCOBOL

Running log of every decision and workaround needed to run the legacy COBOL
batch programs locally and capture golden output. Each entry says **what**,
**why**, and **effect on fidelity**. Two kinds of entry:

- **W-entries** are workarounds: what was done to make the programs run here.
- **D-entries** are divergences: known or suspected places where the golden
  output may differ from what the real mainframe would produce. The
  equivalence harness has to know about these.

## Environment

| Item | Value |
|---|---|
| OS | Ubuntu 24.04, Linux x86-64 (container) |
| Compiler | GnuCOBOL 3.1.2.0 (`apt-get install gnucobol3`) |
| C compiler | gcc 13.3.0 (stubs, clock shim) |
| Indexed file handler | Berkeley DB 5.3 (BDB), built into the Ubuntu package |
| Legacy source | `aws-samples/aws-mainframe-modernization-carddemo` @ `59cc6c2fd7ebd7ef7925cad552a01a4b8b6e4d5e` (2025-10-16) |

## How to run

```bash
python -m runner.run_legacy READACCT          # builds, runs twice, writes golden/READACCT/
python -m unittest discover -s tests -t .     # stub tests
```

`golden/<JOB>/` holds `manifest.json` (toolchain, flags, source hashes, frozen
clock, per-step result, per-dataset sha256 and record counts), `sysout/` (one
file per SYSOUT DD, plus `<STEP>.DISPLAY` for DISPLAY output and
`<STEP>.stderr` when it isn't empty) and `datasets/` (every dataset the job
created or changed). Inputs that come out unchanged are listed in the manifest
with their sha256 but not copied.

---

## Workarounds

### W1. `legacy/` vendored from upstream CardDemo
- **What:** The repo started empty. The uploaded `scout.zip` held `scout/`, its
  README and a generated `out/`, but no COBOL. Scout's README names upstream
  CardDemo as the source, so `app/` from upstream (plus `LICENSE`, `NOTICE`)
  was copied verbatim into `legacy/app` at the commit above.
- **Why:** The programs under test must be the ones Scout planned against.
- **Verification:** `python -m scout.cli plan legacy/app` reproduces `out/plan.json`
  exactly: identical totals (44 programs, 62 copybooks, 46 jobs, 22,904 LOC, 45 units),
  identical unit list, and byte-identical playbooks.
- **Fidelity:** None lost. Nothing under `legacy/` is modified.

### W2. Compile dialect: `-std=ibm`
- **What:** Programs are compiled with `cobc -x -std=ibm` (plus W4 and W5).
- **Why:** CardDemo targets IBM Enterprise COBOL. `-std=ibm` (`ibm.conf` → `ibm-strict.conf`
  + `lax.conf-inc`) changes settings that affect output bytes. Verified against
  the installed config:

  | Setting | default | `-std=ibm` | Why it matters |
  |---|---|---|---|
  | `binary-size` | 1-2-4-8 | 2-4-8 | COMP field widths match IBM |
  | `binary-byteorder` | big-endian | big-endian | same; stubs decode BINARY args big-endian |
  | `binary-truncate` | yes | no | overridden back to yes by W4 |
  | `arithmetic-osvs` / `perform-osvs` / `move-ibm` | no | yes | IBM intermediate-result, PERFORM-exit and MOVE semantics |
  | `hostsign` | no | yes | allows sign nibble X'F' in the NUMERIC class test on signed packed fields |
  | `pretty-display` | yes | no | DISPLAY of numerics closer to IBM (see D3 for what's left) |
  | `assign-clause` | dynamic | external | `ASSIGN TO ACCTFILE` resolves through env vars, used by W9 |
  | `vsam-status` | ignore | ok | VSAM-style file status codes |
- **Fidelity:** Closer to mainframe semantics than the default dialect.

### W3. `_FORTIFY_SOURCE redefined` warning (ignored)
- **What:** Every `cobc` run prints `<command-line>: warning: "_FORTIFY_SOURCE" redefined`.
- **Why:** Ubuntu's GnuCOBOL package bakes `-D_FORTIFY_SOURCE=2` into `COB_CFLAGS`,
  and the system GCC already defines it. This is a C-level warning from the
  packaging, not a COBOL diagnostic.
- **Fidelity:** No effect. `runner/build.py` filters exactly these two lines; any
  other compiler output is kept in `build/logs/` and in the manifest.

### W4. TRUNC: `-fbinary-truncate` (assumed shop default)
- **What:** Added `-fbinary-truncate`, which restores ANSI truncation of BINARY
  items to their PICTURE (IBM `TRUNC(STD)`).
- **Why:** The upstream compile JCL (`samples/proc/BUILDBAT.prc` and
  `scripts/compile_batch.jcl.template`, both in the upstream repo root, outside
  `app/`) runs Enterprise COBOL V6.3 (`IGY.SIGYCOMP.V63`) with
  `PARM=(APOST,LIST,MAP,NUMBER)`, plus `NOSEQ` in BUILDBAT. Neither sets TRUNC,
  so the IBM default `TRUNC(STD)` applies, **assumed shop default**: an
  installation can change the default, and we have no evidence either way.
- **Fidelity:** Matches IBM's default; differs from plain `-std=ibm` (`TRUNC(BIN)`-like).

### W5. Sign representation: `-fsign=EBCDIC`
- **What:** Added `-fsign=EBCDIC`.
- **Why:** The ASCII seed files are EBCDIC datasets translated character by
  character, so signed zoned fields carry IBM overpunch characters: X'C0'-X'C9'
  become `{`, `A`-`I` (positive) and X'D0'-X'D9` become `}`, `J`-`R` (negative).
  Example: `ACCT-CURR-BAL` `00000001940{` = +194.00. With this flag, GnuCOBOL
  reads and writes those same characters, so output records use the same
  convention as the seeds. (GnuCOBOL's default ASCII convention would store
  positives as plain digits and negatives as `p`-`y`.)
- **Fidelity:** Good, with one exception: see D2.

### W6. `COBDATFT` (assembler) ported to C: `runner/stubs/COBDATFT.c`
- **What:** Statement-by-statement port of `legacy/app/asm/COBDATFT.asm`, with
  the record layout from `legacy/app/maclib/COCDATFT.mac` (80 bytes: type 1,
  input date 20, output type 1, output date 20, error message 38). Built as
  `build/lib/COBDATFT.so`; GnuCOBOL finds it through `COB_LIBRARY_PATH` on
  `CALL 'COBDATFT'`.
- **Faithful quirks kept** (both follow from SS instructions with an implicit
  length taking it from the *first* operand):
  1. `CLC COINPDT+4,=C'-'` compares **20 bytes** (L'COINPDT), not 1. It runs
     past the literal into the rest of the literal pool. In practice it never
     matches, so a type-1 input with `-` at byte 5 is **not** rejected:
     `2024-03-15` comes out as `2024--0-3-`.
  2. `MVC COERMSG,=C'INVALID INPUT'` copies **38 bytes** (L'COERMSG): the
     13-byte literal plus 25 bytes past it.
  3. The type-2 separator check is commented out in the source (lines 47-48),
     so type-2 input isn't validated: `20240315xx` comes out as `202431xx`.
  4. `SR R15,R15` on every path: RETURN-CODE is 0 even after an error.
- **Assumption:** There's no LTORG, so the literal pool sits at the end of the
  only CSECT, after `SAVE`. Per HLASM's placement rules, both odd-length
  literals share one alignment group and appear in source order (`'-'`, then
  `'INVALID INPUT'`). The bytes past the end of the CSECT come from whatever
  the binder put next in the load module, and can't be derived from the source.
  They're modelled as X'00' (`COBDATFT_BEYOND_CSECT`, which can be changed at
  build time). Not verified on a real assembler: there's no HLASM here.
- **Reachability:** CardDemo's only caller (CBACT01C) always passes type `'2'`
  and output type `'2'`, so quirks 1, 2 and 4's error path and the assumption
  are never exercised by the golden runs.
- **Tests:** `tests/test_cobdatft.py`, 10 cases, each derived by hand from the
  assembler. They show the port matches this reading of the source; they can't
  prove it matches the real assembler.

### W7. `CEE3ABD` (LE abend service) stubbed: `runner/stubs/CEE3ABD.c`
- **What:** Decodes `ABCODE` and `TIMING` (big-endian fullwords, see W2) and
  prints `CEE3ABD: USER ABEND U0999 TIMING=0` to stderr. It writes the code to
  `$LEGACY_ABEND_FILE` and ends the process with exit code 222. The runner
  reports that as `{"abend": "U0999"}`, skips later steps and applies each DD's
  abnormal disposition (e.g. `DISP=(NEW,CATLG,DELETE)` deletes the dataset), as
  z/OS would.
- **Why:** On z/OS this ends the job step with a user abend, which isn't a
  Unix exit status (a 12-bit code doesn't fit in 8 bits).
- **Fidelity:** TIMING=0 and TIMING=1 behave the same: both exit immediately
  without libcob's file close, so buffered output may be lost. That doesn't
  matter for the golden output, because abnormal disposition normally deletes
  it. Test: `tests/test_cee3abd.py` (real COBOL caller, checks the code and
  that nothing after the CALL runs).

### W8. Frozen clock: `LD_PRELOAD` shim, because `COB_CURRENT_DATE` isn't enough
- **Finding:** GnuCOBOL 3.1.2 does support `COB_CURRENT_DATE`, but only
  partially. Probe results (`FUNCTION CURRENT-DATE`, `ACCEPT FROM DATE/TIME`,
  2-second sleep in between):

  | Setting | Result |
  |---|---|
  | none | real time, ticking |
  | `COB_CURRENT_DATE=20240315123456` | date and HH:MM:SS frozen, **hundredths still from the real clock** (`...12345657`, then `...12345658` in the next run) |
  | `COB_CURRENT_DATE=2024/03/15 12:34:56` | same |
  | `20241503123456` (the `YYYYDDMM` order in runtime.cfg's text, a doc typo) | only a warning, then a **silently wrong date** (2025-03-03) |

  POSTTRAN (CBTRN02C, line 693) writes `FUNCTION CURRENT-DATE`, hundredths
  included, into transaction timestamps, so `COB_CURRENT_DATE` alone would not
  give reproducible output.
- **What:** `runner/clock/frozen_clock.c`, a small preload library. libcob reads
  wall time only through `clock_gettime` and `time` (checked with `nm -D`); the
  shim also covers `gettimeofday`. Realtime clocks return `LEGACY_FROZEN_EPOCH`
  with sub-seconds 0. Monotonic and CPU clocks pass through, so sleeps still
  work. `TZ=UTC` fixes the `+0000` offset. `COB_CURRENT_DATE` isn't set, so
  there's only one source of time.
- **Guard:** If `LD_PRELOAD` can't load a library, the loader just warns and
  runs on the real clock. So before every run the runner executes `CLOCKCHK`
  (a one-line COBOL program) with the real step environment, and refuses to
  continue unless `CURRENT-DATE` is exactly the frozen value, hundredths
  included. The shim also aborts if the epoch variable is missing.
- **Default:** `2026-01-15T12:00:00Z`; override with `--clock`. Recorded in the manifest.
- **Fidelity:** Deterministic. The real job's timestamps would be the run time,
  so the harness must compare timestamp fields against the frozen clock.

### W9. JCL → local files
- **What:** `runner/jcl.py` reads JOB/EXEC/DD statements: columns 1-71
  significant (sequence numbers in 73-80 dropped), continuations, comments,
  in-stream data and IDCAMS command continuation. Each DD name in a program
  step becomes `DD_<ddname>=<path>`, which GnuCOBOL's `ASSIGN EXTERNAL`
  lookup resolves (W2). Datasets live in `build/work/<JOB>/runN/datasets/`,
  one file per DSN.
- **DISP handling:** NEW allocates an empty file (z/OS allocates even if the
  program never opens it); SHR/OLD must exist or be seedable (W10, W11); MOD
  only in IEFBR14 steps (append semantics in program steps aren't
  implemented, so they're rejected). Normal or abnormal disposition (DELETE,
  CATLG, KEEP) runs after each step. GDG relative generations are fixed at
  job start, as on z/OS.
- **Other DDs:** STEPLIB is ignored (programs come from `build/bin`); SYSOUT
  becomes `sysout/<STEP>.<DD>`; DUMMY becomes `/dev/null`; in-stream data is
  written as 80-byte records.
- **Explicitly unsupported** (raise instead of guessing): PROCs/JCLLIB, symbolic
  parameters, IF/SET/INCLUDE, concatenated DDs, COND=, PDS members, utility
  programs other than IEFBR14. All five target jobs avoid these. Of the 38
  JCL files in `legacy/app/jcl` (both `.jcl` and `.JCL`), 5 are rejected by the
  reader. The other 8 of Scout's 46 jobs live in the `app-*` extension
  directories and aren't read. The dataset catalog reads all 38 files and
  fails if two of them define the same cluster differently (none do).

### W10. VSAM KSDS → Berkeley DB indexed files
- **What:** Cluster attributes are read from CardDemo's own IDCAMS JCL
  (`DEFINE CLUSTER ... KEYS(len off) RECORDSIZE(avg max)`), not hand-typed.
  Each KSDS is filled the way CardDemo does it, from the dataset its JCL
  `REPRO`s in (e.g. `ACCTFILE.jcl`: `ACCTDATA.PS` → `ACCTDATA.VSAM.KSDS`).
  The load uses a small COBOL loader, generated per (record length, key offset,
  key length) by `runner/build.py`, with sequential WRITEs. Like REPRO into an
  empty cluster, it fails on out-of-order or duplicate keys instead of sorting.
- **Golden form:** KSDS contents are **unloaded** to fixed-length records in
  key order (like `REPRO` to a PS dataset). That's what gets compared and
  stored, never the BDB file itself, which isn't a mainframe format and may
  not be byte-stable.
- **Not yet handled:** alternate indexes (CARDDATA, CARDXREF and TRANSACT have
  AIXs). None of the five target programs uses them.

### W11. Seed text → fixed-block datasets
- **What:** One record per line, trailing CR dropped, blank-padded to LRECL. A
  line longer than LRECL is an error, never truncated. The LRECL table
  (`runner/catalog.py:SEEDS`) is the only hand-written mapping. Each value was
  checked against `data/EBCDIC` (file size / record count), and after this
  normalisation every seed is exactly the size of its EBCDIC original.
- **Why:** The ASCII files aren't uniform. `cardxref.txt` has trailing blanks
  trimmed (36 → 50 bytes per record), and `tcatbal.txt`, `trancatg.txt` and
  `trantype.txt` have CRLF endings on some lines. The per-file counts are
  recorded in each manifest under `seed_loads`.
- **Fidelity:** After normalisation, 7 of 9 files match their cp037-decoded
  EBCDIC originals byte for byte. For the other 2, see D1.

### W12. Variable-length (RECFM=VB) output format
- **What:** `COB_VARSEQ_FORMAT=0` is set explicitly: each record is prefixed by
  a 2-byte big-endian **data** length and X'0000'.
- **Why:** That's GnuCOBOL's default. It looks like a z/OS RDW, but differs:
  an RDW's length **includes** its own 4 bytes. READACCT's `VBPS` records are
  12 and 39 data bytes, so they're written as `000C0000` and `00270000`
  (an RDW would say `0010` and `002B`).
- **Fidelity:** Record content is the same; the framing differs. The manifest
  decodes and validates it (record count, lengths ≤ LRECL-4). The harness must
  decode VB files with this format rather than as RDWs.

### W13. Output files closed implicitly at GOBACK
- **What:** CBACT01C opens OUTFILE, ARRYFILE and VBRCFILE but never closes them.
  libcob closes them at GOBACK and warns on stderr
  (`libcob: warning: implicit CLOSE of ...`).
- **Fidelity:** Same data as z/OS, where the runtime also closes open files at
  termination. The warnings are kept in `sysout/STEP05.stderr`, which is
  deterministic.

### W14. Where DISPLAY output goes
- **What:** DISPLAY output (on z/OS, written to the SYSOUT DD) is captured from
  stdout into `sysout/<STEP>.DISPLAY`. The SYSOUT/SYSPRINT DD files exist
  because the JCL allocates them, and stay empty unless a program opens them
  as files.

---

## Divergences: where golden output may differ from the mainframe

### D1. ASCII seed data differs from the EBCDIC originals in two files
Seeds are loaded from `legacy/app/data/ASCII` as instructed, but upstream also
ships the original datasets in `data/EBCDIC`. Decoded as cp037 (cp1140 and
cp500 give the same result), they differ in two places:

| Dataset | Record | Field (copybook) | EBCDIC original | ASCII seed | Affects |
|---|---|---|---|---|---|
| ACCTDATA | 49 (acct `00000000049`) | bytes 103-112, `ACCT-ADDR-ZIP` per CVACT01Y | `ZEROAPR   ` | `A000000000` | READACCT SYSOUT (`DISPLAY ACCOUNT-RECORD`); any job that copies the whole record; INTCALC's interest-group lookup |
| DISCGRP | `DEFAULT`, type 07, cat 0001 | interest rate | `00150{` (15.00) | `00000{` | INTCALC |

The other seven files match exactly after W11. Separately, in **both** seed
sets every account record has its group code at the position CVACT01Y calls
`ACCT-ADDR-ZIP`, with blanks in `ACCT-GROUP-ID`. That's upstream's data, not a
translation problem.

### D2. `INITIALIZE` writes unsigned zeros into signed zoned fields
- Under `-fsign=EBCDIC`, `MOVE ZERO`, `MOVE 0` and `COMPUTE` store positive
  zero in `S9(3)V99` as `0000{` (sign nibble C), as IBM does. **`INITIALIZE`**
  (group or elementary) stores `00000`, with no sign. Neither `-O0` nor any
  `cobc` option changes this; it's GnuCOBOL 3.1.2's INITIALIZE shortcut.
- **Hit in READACCT:** `INITIALIZE ARR-ARRAY-REC` before each ARRYFILE record.
  Occurrences 4 and 5 of `ARR-ACCT-CURR-BAL` are never set afterwards, so all
  50 ARRYFILE records have `000000000000` at bytes 69-80 and 88-99, where IBM
  would write `00000000000{`. The COMP-3 fields in the same occurrences are
  correct (`...0C`).
- **To decide:** whether the harness compares those bytes with a "zoned +0 ≡
  unsigned 0" rule, or whether the golden output should be patched.

### D3. DISPLAY of signed numeric items (SYSOUT only)
- IBM DISPLAYs a signed zoned item as its stored bytes (`00000001940{`).
  GnuCOBOL 3.1.2 always shows the digits plus a separate trailing sign
  (`000000019400+`), whatever `pretty-display` is set to; no option or runtime
  setting changes it. DISPLAY of group items (e.g. `ACCOUNT-RECORD`,
  `VBRC-REC2`) shows the raw bytes correctly.
- **Effect:** SYSOUT lines such as `ACCT-CURR-BAL :...` differ in form.
  Datasets aren't affected.

### D4. Working-storage bytes the program never writes
- CBACT01C moves 10 bytes of `CODATECN-0UT-DATE` into `OUT-ACCT-REISSUE-DATE`,
  but COBDATFT only ever writes the first 8. Bytes 9-10 keep their initial
  contents: spaces under GnuCOBOL, which initialises PIC X without VALUE to
  spaces. Enterprise COBOL doesn't define initial contents without a VALUE
  clause, so the mainframe bytes are unknown (often X'00' or spaces).
- **Hit in READACCT:** every PSCOMP record, bytes 77-78 (the reissue date at bytes 69-78 reads `20250520  `).

---

## Per-job status

| Job | Program | Compiles | Runs | Golden | Identical runs | External CALLs |
|---|---|---|---|---|---|---|
| READACCT | CBACT01C | yes, no diagnostics | RC 0 | `golden/READACCT/` | 2 of 2 | COBDATFT (W6), CEE3ABD (W7) |
| READCARD | CBACT02C | not yet | | | | CEE3ABD |
| READXREF | CBACT03C | not yet | | | | CEE3ABD |
| READCUST | CBCUS01C | not yet | | | | CEE3ABD |
| POSTTRAN | CBTRN02C | not yet | | | | CEE3ABD; clock (W8) |

READACCT golden (clock 2026-01-15T12:00:00Z):

| Dataset | Role | Records | sha256 (first 12) |
|---|---|---|---|
| ACCTDATA.PSCOMP (FB 107) | created | 50 | `3e80d1296b3d` |
| ACCTDATA.ARRYPS (FB 110) | created | 50 | `ff033653be45` |
| ACCTDATA.VBPS (VB 84) | created | 100 (lengths 12, 39) | `5031a2ba5fe4` |
| ACCTDATA.VSAM.KSDS | unchanged input | 50 | `00a9fd689c9e` |

**Known for POSTTRAN:** CardDemo loads `TRANSACT.VSAM.KSDS` from
`DALYTRAN.PS.INIT` (`TRANFILE.jcl`), which exists only in `data/EBCDIC`, so
there's no ASCII seed for it. The runner currently stops there with a
"needs upstream" error.
