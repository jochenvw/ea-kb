# Extraction evaluation

This package runs source-grounded extraction, classification and qualification evaluations.
`__init__.py` scores the golden dataset; `benchmarks.py` scores attributed source benchmarks.
`skills.py` uses Pydantic Evals for isolated ontology/scope labeling and gold-routed extraction.
`history.py` stores create-only skill reports and indexes used by `eval list/history`.
`goals.py` checks annual-report goal evidence, abstraction, scope, duplicates and provenance.
`nist.py` scores CSF structure, provisional mappings, provenance and explicit negative decisions.
`dataset.py` materializes split-isolated cases; `frugal.py` applies quality/efficiency gates.
`distillation.py` exports train-only labels; `suite.py` combines code tests and offline/live evals.
Labels are provisional unless independently reviewed; offline rules do not qualify a live model.
Run `uv run ekb eval skills --skill labeling` or `--skill extraction`; live calls require `--live`.
Use `--max-concurrency` to bound cases; `--compare-concurrency` also requires explicit `--live`.
Run `uv run ekb eval goals --compare-strategies`, `uv run ekb eval nist`, or `uv run ekb test`.
Detailed commands, reports and interpretation limits are documented in `docs\evaluation.md`.
