# Repository utilities

`download_test_corpus.py` retrieves the sources in `config\test_corpus.yaml` without inference.

- From the repository root: `uv run python scripts\download_test_corpus.py`.
- Use `--dry-run` to inspect destinations and `--force` to refresh cached originals.
- Each configured URL is downloaded directly; linked pages are not crawled.
- Files and SHA-256/retrieval/failure metadata stay under ignored `data\test-corpus\public`.

`assessment_summary.py REPORT.json` renders `ekb assess` JSON as Markdown, e.g. a CI job summary.

`demo.py` restores the reviewed snapshot into a separate Compose database and starts the existing
web CLI with model providers disabled. `prepare_demo.sql` filters a disposable export copy only;
never apply it to a working database. See `docs\demo.md` for LLM onboarding and refresh instructions.

Publisher refusals and invalid/empty responses are failures, not fabricated corpus documents.
This is separate from application ingestion; regressions live in `tests\unit\test_corpus_downloader.py`.
