# O-COCRDLIC: Online screen COCRDLIC

## Overview
List Credit Cards

- Wave 2, size XL (30.2 pts), estimated 6 Devin session(s)
- Target: Python service (FastAPI on Lambda) + React form replacing BMS map
- Depends on: F-CODECS, F-ONLINE-PLATFORM (import those modules, never re-implement them)

## What's needed from the user
- Recorded 3270 screen sessions (input keystrokes + resulting maps) for this transaction, or approval to test against the spec only.

## Source inventory
- `app/cbl/COCRDLIC.cbl` (cics, 1093 LOC, cyclomatic~172)
- `app/cpy-bms/COCRDLI.CPY` (private copybook: translate inside this unit)
- Shared layouts (from F-CODECS, import only): COCOM01Y, COTTL01Y, CSDAT01Y, CSMSG01Y, CSSTRPFY, CSUSR01Y, CVACT02Y, CVCRD01Y

## Data contract
| Dataset | Access |
|---|---|
| `CARDDATA.VSAM.KSDS` | R |

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
- 1 COMP-3 fields: use Decimal, never float
- 16 GO TOs: unstructured control flow, translate paragraph-by-paragraph
- 6 REDEFINES: overlapping storage, needs explicit union decoding
- date arithmetic: check century/Lilian-day semantics

## Forbidden actions
- Do not modify legacy sources under `app/` (they are the oracle).
- Do not re-implement shared codecs or subprograms; import them.
- Do not mark the unit migrated while any equivalence mismatch remains unexplained.
- Do not use binary floating point for monetary fields.
