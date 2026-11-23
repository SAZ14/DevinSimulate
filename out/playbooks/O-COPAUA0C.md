# O-COPAUA0C: Extension screen COPAUA0C (app-authorization-ims-db2-mq)

## Overview
Card Authorization Decision Program

- Wave 3, size XL (24.9 pts), estimated 5 Devin session(s)
- Target: Python service (FastAPI on Lambda) + React form replacing BMS map
- Depends on: F-CODECS, F-ONLINE-PLATFORM (import those modules, never re-implement them)

## What's needed from the user
- A decision before work starts: needs IMS/DB2/MQ emulation or a target-platform decision first

## Source inventory
- `app/app-authorization-ims-db2-mq/cbl/COPAUA0C.cbl` (cics-mq, 771 LOC, cyclomatic~84)
- `app/app-authorization-ims-db2-mq/cpy/CCPAUERY.cpy` (private copybook: translate inside this unit)
- `app/app-authorization-ims-db2-mq/cpy/CCPAURLY.cpy` (private copybook: translate inside this unit)
- `app/app-authorization-ims-db2-mq/cpy/CCPAURQY.cpy` (private copybook: translate inside this unit)
- Shared layouts (from F-CODECS, import only): CIPAUDTY, CIPAUSMY, CVACT01Y, CVACT03Y, CVCUS01Y

## Data contract
| Dataset | Access |
|---|---|
| `ACCTDATA.VSAM.KSDS` | R |
| `CARDXREF.VSAM.KSDS` | R |
| `CUSTDATA.VSAM.KSDS` | R |

## Procedure
1. Map the BMS mapset to a typed request/response model (fields, lengths, attributes, PF-key actions).
2. Replace pseudo-conversational RETURN TRANSID / COMMAREA with the session model from F-ONLINE-PLATFORM.
3. Translate each paragraph to a handler function; XCTL → route to the target screen's handler.
4. File access goes only through the F-ONLINE-PLATFORM repository layer (READ/STARTBR/READNEXT → keyed queries, REWRITE → optimistic update).
5. Replay recorded screen flows and diff every output field. Error messages must match the legacy text exactly.

## Specifications
- Output must be byte-identical to legacy for every compared record (after the agreed EBCDIC→ASCII normalisation).
- Decimal arithmetic must match COBOL rounding/truncation (ROUNDED vs. default truncation) exactly.
- Return structured output matching the session schema, including equivalence counts.

## Advice (risks detected by Scout)
- 2 REDEFINES: overlapping storage, needs explicit union decoding
- 3 COMP-3 fields: use Decimal, never float
- IMS DL/I calls: hierarchical DB needs a target data-model decision
- MQ messaging: pick SQS/MSK equivalent and preserve message semantics
- writes a TD queue (likely submits batch JCL via internal reader) -- becomes a job trigger

## Forbidden actions
- Do not modify legacy sources under `app/` (they are the oracle).
- Do not re-implement shared codecs or subprograms; import them.
- Do not mark the unit migrated while any equivalence mismatch remains unexplained.
- Do not use binary floating point for monetary fields.
