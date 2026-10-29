# Verification entry points

- `unit`: deterministic domain, parsing, adapters, inference, evaluation and repository-policy checks.
- `integration`: full ingestion and interface flows with disposable PostgreSQL state.
- `contract`: persistence invariants shared by potential backend implementations.
- `fixtures` and `golden`: synthetic ingestion inputs and attributed/synthetic evaluation labels.

Run affected paths with `uv run pytest -q`; see `docs\usage.md` for database/extras setup.
`uv run ekb test` combines all code tests and teacher/offline evals; see `docs\evaluation.md`.
The folder-guide policy test requires Git and a source checkout, but no network or cloud credentials.
Do not treat skipped database/provider paths as verified behavior.
