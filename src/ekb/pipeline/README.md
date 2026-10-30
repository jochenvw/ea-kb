# Incremental ingestion orchestration

`IngestionPipeline` in `__init__.py` coordinates injected connector/parser/model/store contracts.

- `scan` bounds discovery/workers; `_prepare` acquires revisions and `_ingest` claims extraction.
- Staged scans spool metadata to disk and finish acquisition before parallel extraction begins.
- Progress separates known/unknown identities, actual transfers, reuse and metadata-only sources.
- `_execute` reuses parsing/extraction checkpoints and commits exact knowledge/evidence atomically.
- All scans finish inventory/acquisition before labeling and scoped extraction; labels checkpoint
  independently so extractor failures/prompt changes do not repay unchanged labeling.
- Live providers defer enterprise-goal hypotheses until source-level synthesis produces a compact
  evidence-bound set; business-unit goals, subordinate targets and other concepts persist directly.
- Publication assessments cite persisted metadata/text and have their own replay fingerprint.
- `_heartbeat` renews ownership; persistence must reject stale workers.
- Non-dry-run scans hold a renewable guard before discovery; loss cancels work and fences commits.
- Orchestrated scans fail rather than report success for a task still owned by another worker.
- Keep downloads, parsing and inference outside database transactions.
- Sources report absence through the port contract; transport retries belong to their adapters.

Preserve cancellation, unchanged-scan efficiency and historical evidence.
Start with `tests\integration\test_pipeline.py` and `docs\architecture.md`.
