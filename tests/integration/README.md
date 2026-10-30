# Ingestion and interface integration tests

- `test_pipeline.py` exercises incrementality, replay, history, cancellation, concurrency and formats.
- `test_interfaces.py` exercises web/API evidence, real MCP protocol sessions and a headless TUI.
- `test_benchmark_pipeline.py` checks model-free structured ingestion, replay and exact source records.
- `test_workflow.py` covers orchestration, concurrency/resume, reversible alias reviews and scoped authority.
- `test_ontology_schema.py` verifies schema recreation, unrelated-table safety and ontology SQL constraints.
- `test_scan_cli.py` verifies real CLI progress, interruption/crash recovery, status and reset guards.
- Inputs are synthetic/temporary; PostgreSQL is real but isolated in per-test schemas.

Set `EKB_TEST_DATABASE_URL` using `docs\usage.md`, then run `uv run pytest -q tests\integration`.
Do not replace persisted-provenance checks with mocked success responses or live paid-model calls.
