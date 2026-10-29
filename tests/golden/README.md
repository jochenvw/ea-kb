# Golden extraction dataset

`dataset.json` contains expected candidates, modality/scope, exact evidence and duplicate pairs.
Cases distinguish short attributed public excerpts from explicitly synthetic test material.
`benchmarks.json` captures actual ASB/MCSB NS-6 excerpts, a succession notice and WAF policy JSON.
It preserves full-original/excerpt hashes and licenses; reviewed labels distinguish identifier
reuse, related WAF guidance and exact duplicate occurrences. No ISO clause text is included.

Preserve negations, exceptions and source provenance when changing labels; do not fabricate
normative ISO content. Changes intentionally affect the report's dataset hash.
Run the evaluation command documented in `docs\usage.md` and `tests\unit\test_evaluation.py`.
