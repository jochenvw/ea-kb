# PostgreSQL migrations

`env.py` configures Alembic from application settings; `versions` holds ordered schema revisions.

- `ekb init` upgrades to the current head from the project checkout.
- Change ORM models and add a migration together; do not silently create production tables at startup.
- Run `uv run alembic check` against the migrated local database after schema changes.

The schema models live in `src\ekb\adapters\persistence\postgres\models.py`.
Repository tests use disposable schemas independently of the production migration path.
