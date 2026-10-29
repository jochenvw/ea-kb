# PostgreSQL repositories

- `models.py` declares relational constraints/indexes and JSONB domain payloads.
- `__init__.py` implements source, knowledge, lineage and processing repositories.
- Knowledge search uses exclusive UUID cursors and at most 200 concepts per query.
- `Postgres` creates units of work; uncommitted changes roll back on exit.
- `reset` locks and clears only EKB tables transactionally; active scans/leases block fresh starts.
- Knowledge counts query persisted totals without loading the complete knowledge snapshot.
- Processing claims use owner IDs and renewable leases; segment commits must fence stale workers.
- Publication assessments are immutable; semantic versions may be unknown and authority is scoped.
- `benchmarks.py` stores immutable typed benchmark snapshots and validates exact source bindings.
- `documents(control_id=...)` selects matching whole documents, not a trimmed control list.
- Native references resolve only against exact namespace, revision and published OSCAL label aliases.
- Review-only comparisons validate source bindings; embeddings and valid judgments have replay caches.
- `workflow.py` fences stage/check runs and atomically applies append-only decisions/aliases/authority.
- `issues.py` prioritizes scoped issues before limiting and preserves review/reopening history.

Preserve immutable revisions, exact occurrences and idempotent checkpoints.
Schema changes require `migrations` updates; verification starts with
`tests\contract\test_repositories.py` against disposable schemas.
