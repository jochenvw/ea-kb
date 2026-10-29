# PostgreSQL repositories

- `models.py` declares relational constraints/indexes and JSONB domain payloads.
- `__init__.py` implements source, knowledge, lineage and processing repositories.
- `Postgres` creates units of work; uncommitted changes roll back on exit.
- Processing claims use owner IDs and renewable leases; segment commits must fence stale workers.

Preserve immutable revisions, exact occurrences and idempotent checkpoints.
Schema changes require `migrations` updates; verification starts with
`tests\contract\test_repositories.py` against disposable schemas.
