# O-CODATE01: Extension screen CODATE01 (app-vsam-mq)

## Overview
(no description in source header)

- Wave 3, size L (11.4 pts), estimated 2 Devin session(s)
- Target: Python service (FastAPI on Lambda) + React form replacing BMS map
- Depends on: F-ONLINE-PLATFORM (import those modules, never re-implement them)

## What's needed from the user
- A decision before work starts: needs IMS/DB2/MQ emulation or a target-platform decision first

## Source inventory
- `app/app-vsam-mq/cbl/CODATE01.cbl` (cics-mq, 409 LOC, cyclomatic~42)

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
- MQ messaging: pick SQS/MSK equivalent and preserve message semantics

## Forbidden actions
- Do not modify legacy sources under `app/` (they are the oracle).
- Do not re-implement shared codecs or subprograms; import them.
- Do not mark the unit migrated while any equivalence mismatch remains unexplained.
- Do not use binary floating point for monetary fields.
