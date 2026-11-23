# F-ONLINE-PLATFORM: Online platform: COMMAREA session + CICS file access layer

## Overview
CICS screens hand state through COMMAREA and XCTL. Define the session model and data-access layer once so each screen migrates independently.

- Wave 0, size M (5.0 pts), estimated 1 Devin session(s)
- Target: Session-state model replacing COMMAREA pseudo-conversation; repository layer over the keyed stores
- Depends on: nothing (import those modules, never re-implement them)

## What's needed from the user
- A decision before work starts: 

## Source inventory
- Shared layouts (from F-CODECS, import only): COCOM01Y

## Procedure
1. Migrate as a library with an identical call contract (LINKAGE SECTION → typed signature).
2. Port every caller's call site test vector into unit tests.

## Specifications
- Output must be byte-identical to legacy for every compared record (after the agreed EBCDIC→ASCII normalisation).
- Decimal arithmetic must match COBOL rounding/truncation (ROUNDED vs. default truncation) exactly.
- Return structured output matching the session schema, including equivalence counts.

## Advice (risks detected by Scout)
- pseudo-conversational flow (RETURN TRANSID) must become explicit request/session state

## Forbidden actions
- Do not modify legacy sources under `app/` (they are the oracle).
- Do not re-implement shared codecs or subprograms; import them.
- Do not mark the unit migrated while any equivalence mismatch remains unexplained.
- Do not use binary floating point for monetary fields.
