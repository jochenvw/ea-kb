# Command-line entry point

`app` in `__init__.py` is registered as the `ekb` console script by `pyproject.toml`.
`init/reset` manage EKB-owned development state; rebuilds are guarded to pre-v1.
`scan` ingests/resumes; concepts, lineage, sources and comparisons expose shared services.
`pipeline` runs resumable workflows; `workflow.py` registers orchestration and review commands.
`eval skills/list/history` uses bounded Pydantic Evals cases and durable skill history.
Live evaluation requires opt-in; `--compare-concurrency` additionally requires `--live`.
`eval goals/documents/nist` checks source-grounded strategic, mixed-layer and framework cases.
`eval report --latest` displays the newest retained strategic-goal Markdown report.
`eval qualify/verify-corpus/distill/benchmarks` supports model gates, provenance and train-only export.
`test --ui` adds frontend build and local Playwright checks to Python tests/evaluations.
`tui`, `web`, `mcp` and `worker` expose the other process interfaces.
Async use cases enter `Runtime`; keep `asyncio.run` at this synchronous process boundary.
Use `docs\usage.md` and `uv run ekb --help` for command examples; keep handlers thin.
