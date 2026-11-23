# F-CODECS: Shared record-layout codecs

## Overview
Translate the 22 copybooks shared across units into one codec library. Every other unit imports it; this is the contract that makes parallel migration safe.

- Wave 0, size XL (17.5 pts), estimated 3 Devin session(s)
- Target: Python package: fixed-width record dataclasses + encode/decode (Decimal-safe), round-trip tested on seed data
- Depends on: nothing (import those modules, never re-implement them)

## What's needed from the user
- Nothing. Seed data in `app/data/ASCII/` covers every input.

## Source inventory
- Shared layouts (from F-CODECS, import only): CIPAUDTY, CIPAUSMY, COCOM01Y, COTTL01Y, CSDAT01Y, CSMSG01Y, CSMSG02Y, CSSETATY, CSSTRPFY, CSUSR01Y, CSUTLDWY, CVACT01Y, CVACT02Y, CVACT03Y, CVCRD01Y, CVCUS01Y, CVEXPORT, CVTRA01Y, CVTRA05Y, CVTRA06Y, IMSFUNCS, PAUTBPCB

## Procedure
1. For every copybook listed, generate a frozen dataclass plus `decode(bytes|str) -> obj` and `encode(obj) -> str` for the fixed-width record.
2. PIC 9/S9 with V → `decimal.Decimal` with exact scale. COMP-3 → packed-decimal codec. Never use float.
3. REDEFINES → explicit variant views over the same raw bytes; record which view each consumer uses.
4. Round-trip test every seed file in `app/data/ASCII/`: `encode(decode(line)) == line` for 100% of records.
5. Publish as package `carddemo_codecs` with a version; every other unit pins it.

## Specifications
- Output must be byte-identical to legacy for every compared record (after the agreed EBCDIC→ASCII normalisation).
- Decimal arithmetic must match COBOL rounding/truncation (ROUNDED vs. default truncation) exactly.
- Return structured output matching the session schema, including equivalence counts.

## Advice (risks detected by Scout)
- packed decimal (COMP-3) fields in shared layouts
- REDEFINES in shared layouts -- every unit must decode identically

## Forbidden actions
- Do not modify legacy sources under `app/` (they are the oracle).
- Do not re-implement shared codecs or subprograms; import them.
- Do not mark the unit migrated while any equivalence mismatch remains unexplained.
- Do not use binary floating point for monetary fields.
