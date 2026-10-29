# Verification entry points

- `unit`: deterministic domain, parsing, adapters, inference, evaluation and repository-policy checks.
- `integration`: full ingestion and interface flows with disposable PostgreSQL state.
- `contract`: persistence invariants shared by potential backend implementations.
- `fixtures` and `golden`: synthetic ingestion inputs and attributed/synthetic evaluation labels.

Run affected paths with `uv run pytest -q`; see `docs\usage.md` for database/extras setup.
The folder-guide policy test requires Git and a source checkout, but no network or cloud credentials.
Do not treat skipped database/provider paths as verified behavior.
