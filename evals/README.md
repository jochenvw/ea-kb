# Frugal model qualification

`teacher-v1.json` contains GPT-6.1 Sol annotations and synthetic variants of retained public sources.
Source hashes/attributions distinguish actual quotations from generated examples; labels are
teacher-only, not independent human gold or enterprise authority. Source families stay in one split.
`gates.json` defines provisional student/retrieval quality and efficiency requirements.
Run `uv run ekb test` for code tests plus offline evals; `--live` explicitly enables configured models.
See `docs\evaluation.md` for measured results, source verification and training-only export.
