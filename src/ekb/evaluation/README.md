# Extraction evaluation

`__init__.py` runs the golden dataset and writes predictions, metrics and a concise report.
`benchmarks.py` scores attributed actual benchmark excerpts: extraction, reconciliation and lineage.
`workflow.py` reuses retained originals in persistent schema `ekb_phase3_demo`; governance is simulated.
`dataset.py` materializes split-isolated teacher cases; `frugal.py` qualifies quality and efficiency.
`distillation.py` exports train-only labels; `suite.py` combines code tests and offline/live evals.
`skills.py` independently scores ontology/scope labeling and gold-routed extraction on isolated cases.
It also audits document references and scores organizational coverage and mixed ontology layers.
`goals.py` verifies source-page evidence and evaluates goal detection, abstraction, scope,
duplicates, provenance, provisional hierarchy and efficiency.

- Labels live in `tests\golden\dataset.json`; keep real attributed excerpts distinct from synthetic cases.
- Score grounding, ontology class/modality/scope and duplicates; invented mandates receive extra penalties.
- Preserve dataset/provider identity so different runs can be compared honestly.
- Unclassified gold cases measure extraction, not class quality; use the independently labeled skill evals.

Run `uv run ekb eval run --baseline --output data\evaluation-rules.json` from the repository root.
Run `uv run ekb eval goals --compare-strategies` for the source-audited strategic-goal diagnostic.
The offline baseline is not a paid-model benchmark; see `tests\unit\test_evaluation.py`.
Run `uv run ekb eval benchmarks`; `--configured` explicitly opts into the configured small model.
Run `uv run ekb eval workflow` after actual corpus sync/indexing; see `docs\phase3.md`.
Run `uv run ekb test --ui` for Python/browser tests and evals; omit `--ui` for Python-only checks.
Run `uv run ekb eval skills --skill labeling` or `--skill extraction`; `--live` opts into paid models.
Run `uv run ekb eval documents --verify-sources` for annual/NIST passages and ISO metadata.
Frontend checks require Node.js and an installed Playwright browser; no live corpus or model.
See `docs\evaluation.md` for gates and teacher provenance.
