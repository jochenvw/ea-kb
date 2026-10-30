# Frugal model qualification

`teacher-v2.json` contains 18 source-grounded annotations across ten retained public documents.
Fifteen GPT-6.1 Sol labels are carried forward; three supplemental annotations identify Copilot Auto
with model identity unavailable. Source hashes/attributions distinguish exact quotations from
generated examples; all labels remain teacher-only, not independent human gold or enterprise
authority. Source families stay in one split. `gates.json` defines provisional student/retrieval
quality and efficiency requirements.
`skills-v1.json` isolates labeling and scoped extraction: three attributed excerpts plus 13 neutral
synthetic cases, with family/split isolation and provisional ontology labels.
Run `uv run ekb test` for code tests plus offline evals; `--live` explicitly enables configured models.
See `docs\evaluation.md` for measured results, source verification and training-only export.
