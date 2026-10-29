# Codebase Structure

## 1) Top-Level Map

| Path | Purpose | Evidence |
|---|---|---|
| `src\ekb` | Layer-organized application package | `pyproject.toml`, `src\ekb\runtime.py` |
| `tests` | Unit, integration, repository contracts, fixtures and golden labels | `tests\conftest.py`, `tests\golden\dataset.json` |
| `migrations` | Alembic environment and versioned schema | `migrations\env.py`, `migrations\versions` |
| `config` | Local/Azure profiles and downloader manifest | `config\local.yaml`, `config\azure.yaml`, `config\test_corpus.yaml` |
| `sources` | Selected public ingestion manifest | `sources\public.yaml` |
| `scripts` | Standalone corpus retrieval | `scripts\download_test_corpus.py` |
| `deploy` | Local instructions and Azure environment contract | `deploy\local\README.md`, `deploy\azure\container-apps.env.example` |
| `docs` | Architecture, plugin contracts, decisions and codebase reference | `docs\architecture.md`, `docs\plugins.md` |
| `data` | Ignored artifacts, telemetry, evaluation and corpus | `.gitignore`, `Settings.data_dir` |
| Root configuration | Packaging, lockfile, containers and tools | `pyproject.toml`, `uv.lock`, `Dockerfile`, `compose.yaml` |
| Root intent/instructions | Original goal, phase-two requirements and coding rules | `goal.md`, `goal2.md`, `AGENTS.md` |

No separate frontend package, monorepo workspace or committed CI configuration exists.

## 2) Entry Points

`ekb` resolves to `ekb.interfaces.cli:app` through the package console script. Its commands launch
scanning, Textual, Uvicorn/FastAPI, stdio MCP and the periodic worker. Web/MCP factories are
`create_app`/`create_server`. The image defaults to `ekb web`; Compose selects web/worker commands.
The corpus downloader is independent. `ekb init` invokes Alembic explicitly.

## 3) Module Boundaries

| Boundary | Belongs here | Must not be here |
|---|---|---|
| `domain`, `ports` | Semantic values, evidence rules, typed contracts | Provider/transport implementations |
| `pipeline`, `services` | Ingestion and shared inspection | ORM row representation |
| `adapters` | Retrieval, parsing, models, artifacts, persistence | Independent UI business rules |
| `interfaces` | CLI, TUI, HTTP/templates, MCP | Duplicate ingestion/domain implementations |
| `runtime.py` | Dependency construction and ownership | Implicit service location |
| `config`, `observability`, `evaluation` | Settings, telemetry, scoring | Alternate ingestion/persistence stacks |

## 4) Naming and Organization Rules

Python modules use snake_case; types use PascalCase. Many cohesive implementation packages export
classes from `__init__.py`. Imports use absolute `ekb.*` paths. Tests are `test_*.py`; migrations
have revision-prefixed filenames. Organization is layer-first, not one package per feature.

## 5) Evidence

- `pyproject.toml`, `src\ekb\interfaces\cli\__init__.py`, `src\ekb\runtime.py`.
- `src\ekb\interfaces\api\__init__.py`, `src\ekb\interfaces\mcp\__init__.py`.
- `Dockerfile`, `compose.yaml`, `.gitignore`, `AGENTS.md`.
