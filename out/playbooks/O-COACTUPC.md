# O-COACTUPC: Online screen COACTUPC

## Overview
Accept and process ACCOUNT UPDATE                *

- Wave 2, size XL (99.5 pts), estimated 17 Devin session(s)
- Target: Python service (FastAPI on Lambda) + React form replacing BMS map
- Depends on: F-CODECS, F-ONLINE-PLATFORM (import those modules, never re-implement them)

## What's needed from the user
- Recorded 3270 screen sessions (input keystrokes + resulting maps) for this transaction, or approval to test against the spec only.

## Source inventory
- `app/cbl/COACTUPC.cbl` (cics, 3368 LOC, cyclomatic~471)
- `app/cpy-bms/COACTUP.CPY` (private copybook: translate inside this unit)
- `app/cpy/CSLKPCDY.cpy` (private copybook: translate inside this unit)
- `app/cpy/CSUTLDPY.cpy` (private copybook: translate inside this unit)
- Shared layouts (from F-CODECS, import only): COCOM01Y, COTTL01Y, CSDAT01Y, CSMSG01Y, CSMSG02Y, CSSETATY, CSSTRPFY, CSUSR01Y, CSUTLDWY, CVACT01Y, CVACT03Y, CVCRD01Y, CVCUS01Y

## Data contract
| Dataset | Access |
|---|---|
| `ACCTDATA.VSAM.KSDS` | RW |
| `CARDXREF.VSAM.AIX.PATH` | R |
| `CUSTDATA.VSAM.KSDS` | RW |

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
- 4 COMP-3 fields: use Decimal, never float
- 43 REDEFINES: overlapping storage, needs explicit union decoding
- 51 GO TOs: unstructured control flow, translate paragraph-by-paragraph
- date arithmetic: check century/Lilian-day semantics
- 3368 LOC in one program: split into sub-sessions by paragraph section

## Forbidden actions
- Do not modify legacy sources under `app/` (they are the oracle).
- Do not re-implement shared codecs or subprograms; import them.
- Do not mark the unit migrated while any equivalence mismatch remains unexplained.
- Do not use binary floating point for monetary fields.
