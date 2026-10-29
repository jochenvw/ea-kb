# Extraction evaluation

`__init__.py` runs the golden dataset and writes predictions, metrics and a concise report.
`benchmarks.py` scores attributed actual benchmark excerpts: extraction, reconciliation and lineage.
`workflow.py` reuses retained originals in persistent schema `ekb_phase3_demo`; governance is simulated.
`dataset.py` materializes split-isolated teacher cases; `frugal.py` qualifies quality and efficiency.
`distillation.py` exports train-only labels; `suite.py` combines code tests and offline/live evals.

- Labels live in `tests\golden\dataset.json`; keep real attributed excerpts distinct from synthetic cases.
- Score grounding, kind/modality/scope and duplicates; invented mandates receive extra penalties.
- Preserve dataset/provider identity so different runs can be compared honestly.

Run `uv run ekb eval run --baseline --output data\evaluation-rules.json` from the repository root.
The offline baseline is not a paid-model benchmark; see `tests\unit\test_evaluation.py`.
Run `uv run ekb eval benchmarks`; `--configured` explicitly opts into the configured small model.
Run `uv run ekb eval workflow` after actual corpus sync/indexing; see `docs\phase3.md`.
Run `uv run ekb test --ui` for Python/browser tests and evals; omit `--ui` for Python-only checks.
Frontend checks require Node.js and an installed Playwright browser; no live corpus or model.
See `docs\evaluation.md` for gates and teacher provenance.
