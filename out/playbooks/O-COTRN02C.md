# O-COTRN02C: Online screen COTRN02C

## Overview
Add a new Transaction to TRANSACT file

- Wave 2, size XL (20.2 pts), estimated 4 Devin session(s)
- Target: Python service (FastAPI on Lambda) + React form replacing BMS map
- Depends on: F-CODECS, F-SUBPROGS, F-ONLINE-PLATFORM (import those modules, never re-implement them)

## What's needed from the user
- Recorded 3270 screen sessions (input keystrokes + resulting maps) for this transaction, or approval to test against the spec only.

## Source inventory
- `app/cbl/COTRN02C.cbl` (cics, 614 LOC, cyclomatic~107)
- `app/cpy-bms/COTRN02.CPY` (private copybook: translate inside this unit)
- Shared layouts (from F-CODECS, import only): COCOM01Y, COTTL01Y, CSDAT01Y, CSMSG01Y, CVACT01Y, CVACT03Y, CVTRA05Y

## Data contract
| Dataset | Access |
|---|---|
| `CARDXREF.VSAM.AIX.PATH` | R |
| `CARDXREF.VSAM.KSDS` | R |
| `TRANSACT.VSAM.KSDS` | RW |

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
