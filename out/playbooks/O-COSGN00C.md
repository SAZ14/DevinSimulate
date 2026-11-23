# O-COSGN00C: Online screen COSGN00C

## Overview
Signon Screen for the CardDemo Application

- Wave 2, size S (3.8 pts), estimated 1 Devin session(s)
- Target: Python service (FastAPI on Lambda) + React form replacing BMS map
- Depends on: F-CODECS, F-ONLINE-PLATFORM (import those modules, never re-implement them)

## What's needed from the user
- Recorded 3270 screen sessions (input keystrokes + resulting maps) for this transaction, or approval to test against the spec only.

## Source inventory
- `app/cbl/COSGN00C.cbl` (cics, 172 LOC, cyclomatic~20)
- `app/cpy-bms/COSGN00.CPY` (private copybook: translate inside this unit)
- Shared layouts (from F-CODECS, import only): COCOM01Y, COTTL01Y, CSDAT01Y, CSMSG01Y, CSUSR01Y

## Data contract
| Dataset | Access |
|---|---|
| `USRSEC.VSAM.KSDS` | R |

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
- date arithmetic: check century/Lilian-day semantics

## Forbidden actions
- Do not modify legacy sources under `app/` (they are the oracle).
- Do not re-implement shared codecs or subprograms; import them.
- Do not mark the unit migrated while any equivalence mismatch remains unexplained.
- Do not use binary floating point for monetary fields.
