# Application interfaces

- `cli` owns the installed `ekb` command and process launch.
- `tui` provides interactive terminal inspection and scanning.
- `api` serves JSON/HTML and starts configured scans; `web` holds templates and static assets.
- `mcp` exposes read-only structured knowledge tools over stdio.

Use shared `KnowledgeService` and ingestion/runtime composition rather than duplicating business logic.
See each subfolder guide and `tests\integration\test_interfaces.py`.
