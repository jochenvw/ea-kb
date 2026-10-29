# Replaceable processing stages and checks

`indexing.py` adapts the existing ingestion engine without merging or granting authority.
`enrichment.py` registers deterministic and optional semantic checks; `issues.py` registers detectors.
`__init__.py` runs independently cached checks and persists their typed outputs, not their algorithms.
`semantic.py` unions lexical candidates with cached bounded-pool embedding neighbors when configured;
zero lexical overlap does not prevent semantic discovery. Verifier results stay review-only.
Inject stage/check implementations through `StageRegistry`; each declares versions and contracts.
Defaults never enable paid inference or automatic semantic merges. See `docs\phase3.md`.
