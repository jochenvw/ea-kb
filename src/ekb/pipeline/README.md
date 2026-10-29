# Incremental ingestion orchestration

`IngestionPipeline` in `__init__.py` coordinates injected connector/parser/model/store contracts.

- `scan` bounds discovery/workers and records progress; `_process` compares revisions and claims work.
- `_execute` reuses parsing/extraction checkpoints and commits exact knowledge/evidence atomically.
- Publication assessments cite persisted metadata/text and have their own replay fingerprint.
- `_heartbeat` renews ownership; persistence must reject stale workers.
- Keep downloads, parsing and inference outside database transactions.

Preserve cancellation, unchanged-scan efficiency and historical evidence.
Start with `tests\integration\test_pipeline.py` and `docs\architecture.md`.
