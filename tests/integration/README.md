# Ingestion and interface integration tests

- `test_pipeline.py` exercises incrementality, replay, history, cancellation, concurrency and formats.
- `test_interfaces.py` exercises web/API evidence, real MCP protocol sessions and a headless TUI.
- `test_benchmark_pipeline.py` checks model-free structured ingestion, replay and exact source records.
- `test_workflow.py` covers orchestration, real concurrency/resume, review, scoped authority and caches.
- `test_ontology_schema.py` verifies schema recreation, unrelated-table safety and ontology SQL constraints.
- Inputs are synthetic/temporary; PostgreSQL is real but isolated in per-test schemas.

Set `EKB_TEST_DATABASE_URL` using `docs\usage.md`, then run `uv run pytest -q tests\integration`.
Do not replace persisted-provenance checks with mocked success responses or live paid-model calls.
