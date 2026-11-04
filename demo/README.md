# Precomputed offline demo

Run `uv run python scripts\demo.py` from a checkout with uv and Docker's Linux engine running.
It restores `snapshot.zip` once, then serves the populated workspace at http://127.0.0.1:8000.
Use `--port 8001` if another EKB instance is running, or `--restore-only` to stop before serving.
No Foundry, Azure login, model credentials, downloads, parsing or inference are used.

The separate `ekb-demo` Compose project uses PostgreSQL 17 on loopback port 5433 and
ignored `data\demo` artifacts. Reruns preserve demo edits; ordinary EKB storage is untouched.
`manifest.json` pins the archive, source originals, schema code and expected restored counts.
See [licenses and scope](NOTICE.md) and [maintenance/LLM instructions](../docs/demo.md).
Stop the database with `docker compose -p ekb-demo -f demo\compose.yaml stop`.
