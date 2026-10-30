# Schema revision history

- `f9732a9f33fb_initial_schema.py` introduces knowledge, provenance and processing tables.
- `e2a0f34cf236_parse_checkpoints.py` adds durable parsing checkpoints, including empty parses.
- `c9b12e70a641_publication_semver.py` adds publication assessments and nullable semantic versions.
- `bc02fd5aa60d` adds durable pipeline/check runs, findings, decisions, aliases and issue history.

Alembic revision/predecessor identifiers define order; filenames alone do not.
During pre-v1 development these revisions are archived; rebuild from ORM metadata instead.
From v1, establish a release baseline and add revisions rather than rewriting applied migrations.
See `migrations\README.md` for commands and the PostgreSQL adapter for current models.
