# B-READACCT: Batch job READACCT

## Overview
PRE DELETE STEP RUN THE PROGRAM THAT READS THE ACCOUNT MASTER VSAM FILE

- Wave 1, size L (10.2 pts), estimated 2 Devin session(s)
- Target: Python 3.12 batch handler (AWS Lambda / Step Functions), records via shared codecs
- Depends on: F-CODECS (import those modules, never re-implement them)

## What's needed from the user
- Nothing. Seed data in `app/data/ASCII/` covers every input.

## Source inventory
- `app/cbl/CBACT01C.cbl` (batch, 358 LOC, cyclomatic~54)
- `app/cpy/CODATECN.cpy` (private copybook: translate inside this unit)
- Shared layouts (from F-CODECS, import only): CVACT01Y
- JCL: `app/jcl/READACCT.jcl` (utility steps: IEFBR14)

## Data contract
| Dataset | Access |
|---|---|
| `ACCTDATA.ARRYPS` | W |
| `ACCTDATA.PSCOMP` | W |
| `ACCTDATA.VBPS` | W |
| `ACCTDATA.VSAM.KSDS` | R |

## Procedure
1. Read the JCL first: list each step, its program or utility, and its DD → dataset bindings. Reproduce utility steps (SORT/IDCAMS/IEBGENER) in Python with the same keys and record order.
2. Translate the PROCEDURE DIVISION paragraph by paragraph into functions with the same names (snake_case). Keep a comment mapping each function to its source line range.
3. Use `carddemo_codecs` for every record read or written. VSAM KSDS → keyed store interface (dict in tests; DynamoDB adapter in deploy).
4. Preserve FILE STATUS semantics: map each status code the program checks to an explicit exception or return value.
5. Run the equivalence harness: same inputs → legacy (GnuCOBOL) output vs. new output, compared record by record and field by field.
6. Iterate until mismatches = 0. For each mismatch class you fix, add a rule to `playbook_lessons`.

## Specifications
- Output must be byte-identical to legacy for every compared record (after the agreed EBCDIC→ASCII normalisation).
- Decimal arithmetic must match COBOL rounding/truncation (ROUNDED vs. default truncation) exactly.
- Return structured output matching the session schema, including equivalence counts.

## Advice (risks detected by Scout)
- 2 COMP-3 fields: use Decimal, never float
- 2 REDEFINES: overlapping storage, needs explicit union decoding
- LE abend (CEE3ABD): map to explicit error exit codes

## Forbidden actions
- Do not modify legacy sources under `app/` (they are the oracle).
- Do not re-implement shared codecs or subprograms; import them.
- Do not mark the unit migrated while any equivalence mismatch remains unexplained.
- Do not use binary floating point for monetary fields.
