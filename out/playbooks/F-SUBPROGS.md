# F-SUBPROGS: Shared subprograms

## Overview
Called by multiple programs; migrate once as a library before dependents.

- Wave 0, size S (3.4 pts), estimated 1 Devin session(s)
- Target: Python module with identical call contract (LINKAGE SECTION -> typed function signature)
- Depends on: nothing (import those modules, never re-implement them)

## What's needed from the user
- A decision before work starts: 

## Source inventory
- `app/cbl/CSUTLDTC.cbl` (batch, 114 LOC, cyclomatic~11)

## Procedure
1. Migrate as a library with an identical call contract (LINKAGE SECTION → typed signature).
2. Port every caller's call site test vector into unit tests.

## Specifications
- Output must be byte-identical to legacy for every compared record (after the agreed EBCDIC→ASCII normalisation).
- Decimal arithmetic must match COBOL rounding/truncation (ROUNDED vs. default truncation) exactly.
- Return structured output matching the session schema, including equivalence counts.

## Advice (risks detected by Scout)
- 3 REDEFINES: overlapping storage, needs explicit union decoding
- date arithmetic: check century/Lilian-day semantics

## Forbidden actions
- Do not modify legacy sources under `app/` (they are the oracle).
- Do not re-implement shared codecs or subprograms; import them.
- Do not mark the unit migrated while any equivalence mismatch remains unexplained.
- Do not use binary floating point for monetary fields.
