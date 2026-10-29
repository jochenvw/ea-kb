# Coding Conventions

`AGENTS.md` is the authoritative instruction file; this maps its rules to implementation/tooling.

## 1) Naming Rules

| Item | Rule | Example | Evidence |
|---|---|---|---|
| Files/modules | snake_case and cohesive package exports | `download_test_corpus.py`, adapter `__init__.py` | `scripts`, `src\ekb\adapters` |
| Functions/variables | snake_case; underscore for internal helpers | `_processing_fingerprint`, `source_id` | Pipeline |
| Types/protocols | PascalCase | `SourceRevision`, `UnitOfWorkFactory` | Domain models and ports |
| Constants/environment | Uppercase, `EKB_` settings prefix | `PROMPT_VERSION`, `EKB_MODEL_ID` | Inference adapter, `Settings` |

## 2) Formatting and Linting

Ruff targets Python 3.13, formats to 100-character lines and enables `E`, `F`, `I`, `UP`, `B`,
`ASYNC`. basedpyright checks `src`, `tests`, `scripts`, strictly for domain/ports and standard
elsewhere. `.gitattributes` normalizes text to LF. Commands are in `AGENTS.md`.

## 3) Import and Module Conventions

Source uses absolute `ekb.*` imports, grouped by Ruff's import rule. Ports expose `Protocol`
contracts and domain values, not adapter instances or ORM rows. `Runtime` composes dependencies.
Optional framework/Monitor imports occur on selected paths. Existing package-level implementations
are not a requirement to grow those modules indefinitely.

## 4) Error and Logging Conventions

Domain validation raises `ValueError`/`GroundingError`. The pipeline aggregates sanitized
per-source failures and continues other work; cancellation propagates. The CLI fails partial scans;
HTTP uses explicit status errors. Transport retries and model-validation retries have separate bounds.

structlog emits rotating JSONL with timestamps and trace/span correlation. Application spans
record exception type/status, not sensitive messages. Model payloads are separate, opt-in debug
artifacts without authorization headers.

## 5) Testing Conventions

Use `tests\unit`, `tests\integration`, `tests\contract`, `test_*.py`, pytest assertions and async
tests. HTTPX `MockTransport`, injected adapters, `monkeypatch` and temporary paths isolate effects.
PostgreSQL fixtures create/drop unique schemas. Add behavior-oriented regressions; no numerical
coverage threshold is configured.

## 6) Evidence

- `AGENTS.md`, `pyproject.toml`, `.gitattributes`.
- `src\ekb\domain\grounding.py`, `src\ekb\ports\__init__.py`, `src\ekb\runtime.py`.
- `src\ekb\pipeline\__init__.py`, `src\ekb\services\retry.py`.
- `src\ekb\observability\__init__.py`, `src\ekb\adapters\inference\__init__.py`.
- `tests\conftest.py`, `tests\unit\test_sources.py`, `tests\unit\test_corpus_downloader.py`.
