# Extraction evaluation

`__init__.py` runs the golden dataset and writes predictions, metrics and a concise report.
`benchmarks.py` scores attributed actual benchmark excerpts: extraction, reconciliation and lineage.

- Labels live in `tests\golden\dataset.json`; keep real attributed excerpts distinct from synthetic cases.
- Score grounding, kind/modality/scope and duplicates; invented mandates receive extra penalties.
- Preserve dataset/provider identity so different runs can be compared honestly.

Run `uv run ekb eval run --baseline --output data\evaluation-rules.json` from the repository root.
The offline baseline is not a paid-model benchmark; see `tests\unit\test_evaluation.py`.
Run `uv run ekb eval benchmarks`; `--configured` explicitly opts into the configured small model.
