# Testing Patterns

## 1) Test Stack and Commands

pytest 9.1.1, pytest-asyncio 1.4.0 and Hypothesis 6.170.5 are locked development dependencies.
Tests use pytest assertions, fixtures, `monkeypatch` and injected clients. Async mode is automatic.
The folder-guide policy test additionally requires Git and a source checkout; it does not use
network access or cloud credentials.

```powershell
uv run pytest -q tests\unit
uv run pytest -q tests\integration tests\contract
uv run pytest -q
uv run ekb eval run --baseline --output data\evaluation-rules.json
```

Database cases require `EKB_TEST_DATABASE_URL`; see `docs\usage.md`. No coverage command is configured.

## 2) Test Layout

`tests\unit` covers domain, parsing, retrieval, model validation, adapters, evaluation and downloader.
`tests\integration` covers ingestion/interfaces; `tests\contract` covers PostgreSQL semantics.
`tests\conftest.py` owns database setup. Fixtures and golden labels have separate subdirectories.
Test files and functions use `test_` names.

## 3) Test Scope Matrix

| Scope | Covered? | Typical target | Notes |
|---|---|---|---|
| Unit/property | Yes | Identity, grounding, parsing, HTTP/model handling | No live cloud |
| Repository contract | Yes | Commit/rollback, evidence, versions, authority, leases, cycles | Real disposable PostgreSQL schema |
| Pipeline integration | Yes | Incrementality, resume, cancellation, concurrent workers, Office/PDF | Temporary sources and real database |
| Interface integration | Yes | HTML/API, scan form, MCP protocol, TUI interaction | ASGI transport, SDK memory session, Textual headless pilot |
| Deployed/browser end-to-end | Not in the committed suite | External deployment, identity, live exporters | Transport tests do not establish live deployment |
| Evaluation | Yes | Extraction, scope, modality, grounding and duplicates | Offline baseline or configured model |

## 4) Mocking and Isolation Strategy

HTTPX `MockTransport` supplies responses; Azure clients and optional framework calls are injected
or monkeypatched. `tmp_path` isolates files. Database fixtures create UUID-named schemas, dispose
connections and drop only that schema.

Without `EKB_TEST_DATABASE_URL`, dependent cases skip. Framework cases can skip without the optional
package. Offline-only success does not verify skipped paths. Publisher refusal is an availability
result, not a deterministic unit-test failure.

## 5) Coverage and Quality Signals

No coverage gate or CI pipeline is committed. [TODO] Measured coverage and load/soak limits are
not established. Test counts are intentionally not frozen here. Use `AGENTS.md` for quality gates
and Alembic drift checks for persistence changes.

Golden reports include dataset hash/provider identity. Exact substring grounding is not proof of
semantic entailment; offline rules are not a real-model benchmark. Live cloud identity/export and
paid inference remain outside offline contracts.

## 6) Evidence

- `pyproject.toml`, `uv.lock`, `AGENTS.md`, `tests\conftest.py`.
- `tests\unit\test_domain.py`, `tests\unit\test_inference.py`, `tests\unit\test_adapters.py`.
- `tests\unit\test_corpus_downloader.py`, `tests\contract\test_repositories.py`.
- `tests\integration\test_pipeline.py`, `tests\integration\test_interfaces.py`.
- `src\ekb\evaluation\__init__.py`, `tests\golden\dataset.json`, `docs\usage.md`.
