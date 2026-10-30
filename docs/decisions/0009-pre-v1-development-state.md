# 0009: Disposable state before application v1

Status: accepted.

Application release `0.1.0` is registered in `pyproject.toml`; Python, CLI and API read installed
package metadata rather than maintaining independent application version constants.

While the application major version is below 1, backwards-incompatible changes and deletion/recreation
of EKB-owned data and schemas are explicitly permitted. Compatibility migrations are not required.
`ekb init` creates missing tables from current ORM metadata; `ekb init --recreate` rebuilds owned
tables without starting ingestion. Active scans/leases block reset. Source originals, inputs,
configuration, reports/logs and unrelated tables/resources survive the reset.
Normal application startup never deletes data.

From major version 1 onward, destructive development initialization is disabled and schema evolution
requires explicit migrations. The archived pre-v1 Alembic revisions are not the development bootstrap.
