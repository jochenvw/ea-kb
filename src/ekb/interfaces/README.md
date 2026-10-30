# Application interfaces

- `cli` owns the installed `ekb` command and process launch.
- `tui` provides interactive knowledge and governance inspection.
- `api` serves JSON and the prebuilt browser shell; `web` holds packaged static assets.
- `mcp` exposes read-only structured knowledge tools over stdio.

Ingestion execution, status and resume belong to the CLI/worker, not web or TUI lifespans.
Use shared `KnowledgeService` and ingestion/runtime composition rather than duplicating business logic.
See each subfolder guide and `tests\integration\test_interfaces.py`.
