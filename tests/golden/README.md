# Golden extraction dataset

`dataset.json` contains expected candidates, modality/scope, exact evidence and duplicate pairs.
Cases distinguish short attributed public excerpts from explicitly synthetic test material.
Source-grounded skill cases, including exact publisher excerpts and hashes, are in `evals\skills-v1.json`.
No ISO clause text is included.

Preserve negations, exceptions and source provenance when changing labels; do not fabricate
normative ISO content. Changes intentionally affect the report's dataset hash.
Run the evaluation command documented in `docs\usage.md` and `tests\unit\test_evaluation.py`.
