# EKB application package

- `runtime.py` constructs providers, shared services and resource lifetimes.
- `__init__.py` reads the application version registered in `pyproject.toml` via package metadata.
- `maintenance.py` runs guarded database resets without deleting originals or source inputs.
- `domain` and `ports` define values, invariants and interchangeable contracts.
- `pipeline` adapts ingestion; `orchestration` orders durable, replaceable `stages`.
- `services` shares inspection, reconciliation, strict resolution and issue review.
- `adapters` implement infrastructure; `interfaces` expose CLI, terminal, web and MCP workflows.
- `config`, `observability` and `evaluation` own their respective cross-cutting concerns.

Start at the relevant subfolder guide, not every module.
Keep infrastructure out of domain values; see root `AGENTS.md` and `docs\architecture.md`.
