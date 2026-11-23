# B-CBIMPORT: Batch job CBIMPORT

## Overview
IMPORT CUSTOMER DATA FROM MULTI-RECORD EXPORT FILE AND SPLIT INTO SEPARATE NORMALIZED FILES FOR TARGET SYSTEM INPUT EXPORT FILE OUTPUT NORMALIZED FILES ERROR OUTPUT FILE SYSTEM OUTPUT //

- Wave 1, size L (11.8 pts), estimated 2 Devin session(s)
- Target: Python 3.12 batch handler (AWS Lambda / Step Functions), records via shared codecs
- Depends on: F-CODECS (import those modules, never re-implement them)

## What's needed from the user
- Golden input datasets: inputs produced by B-CBEXPORT -- run legacy upstream on GnuCOBOL to capture golden inputs

## Source inventory
- `app/cbl/CBIMPORT.cbl` (batch, 337 LOC, cyclomatic~42)
- Shared layouts (from F-CODECS, import only): CVACT01Y, CVACT02Y, CVACT03Y, CVCUS01Y, CVEXPORT, CVTRA05Y
- JCL: `app/jcl/CBIMPORT.jcl` (utility steps: none)

## Data contract
| Dataset | Access |
|---|---|
| `ACCTDATA.IMPORT` | W |
| `CARDXREF.IMPORT` | W |
| `CUSTDATA.IMPORT` | W |
| `EXPORT.DATA` | R |
| `IMPORT.ERRORS` | W |
| `TRANSACT.IMPORT` | W |

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
- LE abend (CEE3ABD): map to explicit error exit codes
- date arithmetic: check century/Lilian-day semantics

## Forbidden actions
- Do not modify legacy sources under `app/` (they are the oracle).
- Do not re-implement shared codecs or subprograms; import them.
- Do not mark the unit migrated while any equivalence mismatch remains unexplained.
- Do not use binary floating point for monetary fields.
