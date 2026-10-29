# Local profile

From the project root: `uv sync`, `docker compose up -d postgres --wait`, `uv run ekb init`.
Copy `.env.example` to `.env` only when overrides are needed. Do not commit credentials.

Run `uv run ekb scan tests\fixtures\local`, then `uv run ekb tui --directory tests\fixtures\local`.
For the web scan button, set `EKB_SOURCE_DIRECTORY` to an authorized local source directory.
`uv run ekb web` binds to localhost by default.

For containers, first initialize the database, then run `docker compose --profile app up --build -d`.
See the root README for migration jobs and the full verification loop.
