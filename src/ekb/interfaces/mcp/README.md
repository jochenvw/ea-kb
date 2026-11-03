# Model Context Protocol server

`create_server` in `__init__.py` builds the official FastMCP server with explicit tool schemas.

- Knowledge tools search, inspect, trace and resolve concepts and governance.
- Solution tools list/get immutable architecture revisions; `assess_solution` verifies design JSON and
  an ARM template against an approved baseline and retains the report for the solution page.
- Portfolio tools `exception_risk` / `change_impact` return read-only projections with denominators.
- Calls delegate to `KnowledgeService`; runtime resources belong to server lifespan.
- Stdio stdout is protocol-only; application logs go to configured telemetry files.
- `resolve_knowledge` / `resolve_requirement` default to strict canonical, never candidate fallback.
- `list_knowledge_issues` / `get_reconciliation_history` expose read-only governance context.

Assessment inspects compiled ARM only; unsupported checks stay unknown. Retaining a report decides nothing.
Do not add mutation or autonomous compliance decisions without an explicit requirement.
Protocol verification uses the SDK's in-memory session in `tests\integration\test_interfaces.py`.
