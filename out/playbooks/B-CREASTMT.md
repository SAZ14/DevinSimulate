# B-CREASTMT: Batch job CREASTMT

## Overview
This JCL will create statement for each CARD present in the XREF file CREATE COPY OF TRANSACT FILE WITH CARD NUMBER AND TRAN ID AS KEY DELETE TRANSACTION REPORTS FROM PREVIOUS RUN PRODUCING REPORT IN TEXT AND HTML - DEMONSTRATES CALLED SUBROUTINE

- Wave 1, size XL (31.6 pts), estimated 6 Devin session(s)
- Target: Python 3.12 batch handler (AWS Lambda / Step Functions), records via shared codecs
- Depends on: F-CODECS (import those modules, never re-implement them)

## What's needed from the user
- Golden input datasets: inputs produced by B-POSTTRAN -- run legacy upstream on GnuCOBOL to capture golden inputs

## Source inventory
- `app/cbl/CBSTM03A.CBL` (batch, 784 LOC, cyclomatic~55)
- `app/cbl/CBSTM03B.CBL` (batch, 162 LOC, cyclomatic~31)
- `app/cpy/COSTM01.CPY` (private copybook: translate inside this unit)
- `app/cpy/CUSTREC.cpy` (private copybook: translate inside this unit)
- Shared layouts (from F-CODECS, import only): CVACT01Y, CVACT03Y
- JCL: `app/jcl/CREASTMT.jcl` (utility steps: IDCAMS, IEFBR14, SORT)

## Data contract
| Dataset | Access |
|---|---|
| `STATEMNT.HTML` | W |
| `STATEMNT.PS` | W |
| `TRANSACT.VSAM.KSDS` | R |
| `TRXFL.SEQ` | W |
| `TRXFL.VSAM.KSDS` | W |

Intermediates created and consumed inside the job: TRXFL.SEQ. Keep them as in-memory or temp files; don't persist them as new tables.

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
- 1 COMP-3 fields: use Decimal, never float
- 13 GO TOs: unstructured control flow, translate paragraph-by-paragraph
- 14 GO TOs: unstructured control flow, translate paragraph-by-paragraph
- LE abend (CEE3ABD): map to explicit error exit codes
- DFSORT step: reproduce sort keys/collation exactly (EBCDIC vs ASCII order)

## Forbidden actions
- Do not modify legacy sources under `app/` (they are the oracle).
- Do not re-implement shared codecs or subprograms; import them.
- Do not mark the unit migrated while any equivalence mismatch remains unexplained.
- Do not use binary floating point for monetary fields.
