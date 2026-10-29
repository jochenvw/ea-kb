# Ingestion and interface integration tests

- `test_pipeline.py` exercises incrementality, replay, history, cancellation, concurrency and formats.
- `test_interfaces.py` exercises web/API evidence, real MCP protocol sessions and a headless TUI.
- Inputs are synthetic/temporary; PostgreSQL is real but isolated in per-test schemas.

Set `EKB_TEST_DATABASE_URL` using root README, then run `uv run pytest -q tests\integration`.
Do not replace persisted-provenance checks with mocked success responses or live paid-model calls.
