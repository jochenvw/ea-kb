# Command-line entry point

`app` in `__init__.py` is registered as the `ekb` console script by `pyproject.toml`.

- `init` migrates; `scan` ingests/resumes; inspection commands return persisted knowledge.
- `eval`, `doctor`, `tui`, `web`, `mcp` and `worker` expose evaluation, diagnostics and process launch.
- Async use cases enter `Runtime`; keep `asyncio.run` at this synchronous process boundary.

Use `docs\usage.md` command examples and `uv run ekb --help`.
Do not move ingestion/business rules into Typer handlers; share the existing services.
