# EKB application package

- `runtime.py` constructs providers, shared services and resource lifetimes.
- `domain` and `ports` define values, invariants and interchangeable contracts.
- `pipeline` and `services` coordinate ingestion and inspection.
- `adapters` implement infrastructure; `interfaces` expose CLI, terminal, web and MCP workflows.
- `config`, `observability` and `evaluation` own their respective cross-cutting concerns.

Start at the relevant subfolder guide, not every module.
Keep infrastructure out of domain values; see root `AGENTS.md` and `docs\architecture.md`.
