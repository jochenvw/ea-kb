# Local profile

From the project root: `uv sync`, `docker compose up -d postgres --wait`, `uv run ekb init`.
Copy `.env.example` to `.env` only when overrides are needed. Do not commit credentials.

Run `uv run ekb pipeline run --directory tests\fixtures\local`, then inspect with
`uv run ekb pipeline status`, `uv run ekb tui` or `uv run ekb web`.
Web binds to localhost and never starts or owns ingestion. The optional worker scans independently.

For containers, first initialize the database, then run `docker compose --profile app up --build -d`.
See `docs\usage.md` for explicit schema initialization and the full verification loop.
