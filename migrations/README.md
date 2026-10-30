# PostgreSQL migration history

`env.py` configures Alembic from application settings; `versions` holds ordered schema revisions.

- Before application v1, `ekb init` uses current ORM metadata rather than applying revisions.
- Use `uv run ekb init --recreate` after breaking development schema changes; data loss is permitted.
- From v1 onward, establish the release baseline and use explicit migrations and `alembic check`.
- Normal application startup never creates, migrates or drops tables.

The schema models live in `src\ekb\adapters\persistence\postgres\models.py`.
Repository tests use disposable schemas independently of the production migration path.
