# Application interfaces

- `cli` owns the installed `ekb` command and process launch.
- `tui` provides interactive terminal inspection and scanning.
- `api` serves JSON and the prebuilt browser shell; `web` holds packaged static assets.
- `mcp` exposes read-only structured knowledge tools over stdio.

Use shared `KnowledgeService` and ingestion/runtime composition rather than duplicating business logic.
See each subfolder guide and `tests\integration\test_interfaces.py`.
