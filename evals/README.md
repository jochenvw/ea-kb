# Frugal model qualification

`teacher-v2.json` contains 18 source-grounded annotations across ten retained public documents.
Fifteen GPT-6.1 Sol labels are carried forward; three supplemental annotations identify Copilot Auto
with model identity unavailable. Source hashes/attributions distinguish exact quotations from
generated examples; all labels remain teacher-only, not independent human gold or enterprise
authority. Source families stay in one split. `gates.json` defines provisional student/retrieval
quality and efficiency requirements.
`skills-v1.json` isolates labeling and scoped extraction: three attributed excerpts plus 13 neutral
synthetic cases, with family/split isolation and provisional ontology labels.
`goals` contains provisional annual-report goal/target/non-goal annotations with exact page evidence.
`documents-v1.json` reuses those skill contracts for selected Shell annual-report and NIST CSF
passages plus an explicitly metadata-only ISO negative. It separates company-level coverage,
mixed ontology classes and named organizational scope; no standard is automatically adopted.
Original PDF hashes/pages can be audited with `uv run ekb eval documents --verify-sources`.
`nist-csf` imports the supplied Core hierarchy, 25 provisional mappings and 14 negative challenges,
with separately source-audited excerpts, locator corrections and explicit reference disagreements.
Run `uv run ekb test` for code tests plus offline evals; `--live` explicitly enables configured models.
See `docs\evaluation.md` for measured results, source verification and training-only export.
