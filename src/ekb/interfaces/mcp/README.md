# Read-only Model Context Protocol server

`create_server` in `__init__.py` builds the official FastMCP server with explicit tool schemas.

- Tools: `search_concepts`, `get_concept`, `trace_provenance`, `find_related_requirements`.
- Benchmark tools: `get_benchmark_control`, `get_benchmark_edition`, `compare_benchmark_editions`,
  `trace_benchmark_lineage`; all inspect without model calls or proposal writes.
- Calls delegate to `KnowledgeService`; runtime resources belong to server lifespan.
- Stdio stdout is protocol-only; application logs go to configured telemetry files.
- `resolve_knowledge` / `resolve_requirement` default to strict canonical, never candidate fallback.
- `list_knowledge_issues` / `get_reconciliation_history` expose read-only governance context.

Do not add mutation or autonomous compliance decisions without an explicit requirement.
Protocol verification uses the SDK's in-memory session in `tests\integration\test_interfaces.py`.
