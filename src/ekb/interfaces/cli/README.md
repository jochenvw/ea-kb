# Command-line entry point

`app` in `__init__.py` is registered as the `ekb` console script by `pyproject.toml`.

- `init` migrates; `scan` ingests/resumes; inspection commands return persisted knowledge.
- `concepts phrase` caches concise assertions using the configured model; `--source-id` restricts inputs.
- `concepts list --level implementation` separates platform requirements from goals/guidance.
- `sources sync microsoft-security-benchmark` downloads and normalizes actual published assets.
- `compare` persists review-only comparisons; `lineage` accepts UUIDs or `mcsb-v2:NS-6`.
- `workflow.py` adds `pipeline run/status/resume`, `reconcile`, `issues` and strict `knowledge` groups.
- `pipeline run` supports source/profile selection, guarded `--fresh`, `--json` and saved reports.
- `presentation.py` renders ASCII branding, file/check progress, accurate summaries and safe links.
- `eval workflow` demonstrates retained real sources and explicitly simulated isolated governance.
- `evaluation.py` adds `test`, `eval qualify/verify-corpus/distill`; live calls require explicit opt-in.
- `test --ui` includes frontend checks/build and local Playwright regressions before Python/evals.
- `eval`, `doctor`, `tui`, `web`, `mcp` and `worker` expose evaluation, diagnostics and process launch.
- Async use cases enter `Runtime`; keep `asyncio.run` at this synchronous process boundary.

Use `docs\usage.md` command examples and `uv run ekb --help`.
Do not move ingestion/business rules into Typer handlers; share the existing services.
