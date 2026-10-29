# Technology Stack

## 1) Runtime Summary

| Area | Value | Evidence |
|---|---|---|
| Language/runtime | Python `>=3.13,<3.14` | `pyproject.toml` |
| Package manager | uv; container copies `0.11.17` | `uv.lock`, `Dockerfile` |
| Build/layout | Hatchling, `src` layout, `ekb` console script | `pyproject.toml` |
| Database/container | PostgreSQL 17; Python `3.13-slim` image | `compose.yaml`, `Dockerfile` |

## 2) Production Frameworks and Dependencies

Versions are the committed lockfile snapshot through `9ea2a35`. Manifest ranges govern future
resolution; `uv.lock` is authoritative.

| Dependencies | Locked versions | Role | Evidence |
|---|---|---|---|
| Pydantic / pydantic-settings | 2.14.1 / 2.16.1 | Validated domain and settings | `pyproject.toml`, `uv.lock` |
| SQLAlchemy / asyncpg / Alembic | 2.1.6 / 0.32.0 / 1.20.1 | Async persistence and migration | PostgreSQL adapter, `uv.lock` |
| HTTPX / PyYAML / Beautiful Soup | 0.28.1 / 6.0.3 / 4.15.0 | HTTP, manifests and HTML | Source/parser adapters, `uv.lock` |
| PyMuPDF / python-docx / python-pptx | 2.0 / 1.2.0 / 1.0.2 | PDF/Office parsing | Parser adapter, `uv.lock` |
| Typer / Textual | 0.27.3 / 8.2.8 | CLI/TUI | Interfaces, `uv.lock` |
| FastAPI / Uvicorn / Jinja2 / python-multipart | 0.159.1 / 0.54.0 / 3.1.6 / 0.0.32 | HTTP, pages and forms | API interface, `uv.lock` |
| MCP | 1.30.0 | FastMCP server | MCP interface, `uv.lock` |
| structlog / OpenTelemetry SDK | 26.1.0 / 1.45.1 | Logs and spans | Observability, `uv.lock` |
| OpenTelemetry FastAPI instrumentation | 0.66b1 | Request spans | API interface, `uv.lock` |
| Azure Blob / Azure Identity | 12.31.0 / 1.27.0 | Artifacts and credentials | Artifact/inference adapters, `uv.lock` |
| Azure Monitor extra | 1.8.11 | Optional cloud exporters | `pyproject.toml`, `uv.lock` |
| Agent Framework core / OpenAI extra | 1.22.0 / 1.17.0 | Optional chat transport | `pyproject.toml`, `uv.lock` |

Blob/Identity SDKs are base dependencies, although local operation needs no Azure service.
React, TypeScript, Vite, Tailwind and actual shadcn/ui primitives are locked in `frontend\package-lock.json`.
Committed local assets and bundled licenses ship with Python; Node.js is required only to rebuild
or run browser tests. Tesseract/English language data are image system packages.
Docling is not installed; its dependency fallback is documented.

## 3) Development Toolchain

pytest 9.1.1 and pytest-asyncio 1.4.0 run tests; Hypothesis 6.170.5 provides property tests.
Ruff 0.17.3 handles lint/format; basedpyright 1.40.2 checks types. All are development dependencies
in `pyproject.toml`/`uv.lock`, not application frameworks.

## 4) Key Commands

```powershell
uv sync
uv run ekb init
docker compose --profile app build
uv run pytest -q
uv run ruff check src tests migrations scripts
uv run basedpyright
```

Database tests need the setup in `docs\usage.md`; optional extras use `uv sync --all-extras`.

## 5) Environment and Config

`Settings` reads explicit constructor values, `EKB_` environment, `.env`, selected YAML, file secrets
and defaults, in that order. `EKB_CONFIG_FILE` selects the YAML profile. Local defaults use
Compose PostgreSQL and offline rules. Model providers need real endpoint/model configuration;
Azure artifacts need an account URL and pre-existing container. Secrets use `SecretStr`.

## 6) Evidence

- `pyproject.toml`, `uv.lock`, `.python-version`, `Dockerfile`, `compose.yaml`.
- `src\ekb\config\__init__.py`, `config\local.yaml`, `config\azure.yaml`, `.env.example`.
- `frontend\package.json`, `frontend\components.json`, `frontend\package-lock.json`.
- `src\ekb\interfaces\web\static\ui\LICENSES.txt`.
- `docs\decisions\0002-runtime-and-dependencies.md`.
