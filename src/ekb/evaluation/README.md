# Extraction evaluation

This package runs source-grounded extraction, classification and qualification evaluations.
`__init__.py` runs grounded extraction cases and scores aggregate cross-case metrics.
`skills.py` uses Pydantic Evals for isolated ontology/scope labeling and gold-routed extraction.
Its retry totals use provider measurements; incomplete request or retry accounting stays unknown.
`limits.py` coordinates optional per-request, per-deployment quota reservations for skill runs.
`history.py` atomically publishes create-only reports and recoverable typed indexes for `eval list/history`.
`goals.py` checks annual-report goal evidence, abstraction, scope, duplicates and provenance.
Hierarchical strategy reconciliation processes every extracted strategic-goal level. Enterprise goals stay
compact, while distinct business-unit goals and subordinate targets remain available to abstraction,
scope and duplicate metrics without promotion.
`nist.py` scores CSF structure, provisional mappings, provenance and explicit negative decisions.
`dataset.py` materializes split-isolated cases; `frugal.py` applies status and metering-aware gates.
`runner.py` batches sequential Pydantic Evals cases using opaque IDs, not source text or vectors.
`distillation.py` exports train-only labels; `suite.py` combines code tests and offline/live evals.
Labels are provisional unless independently reviewed; offline rules do not qualify a live model.
Run `uv run ekb eval skills --skill labeling` or `--skill extraction`; live calls require `--live`.
Use `--max-concurrency` to bound cases; `--compare-concurrency` also requires explicit `--live`.
Supply `--requests-per-minute` and `--tokens-per-minute` to coordinate configured quotas locally.
Run `uv run ekb eval goals --compare-strategies`, `uv run ekb eval nist`, or `uv run ekb test`.
Detailed commands, reports and interpretation limits are documented in `docs\evaluation.md`.
