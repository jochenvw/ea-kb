# Command-line entry point

`app` in `__init__.py` is registered as the `ekb` console script by `pyproject.toml`.
`init/reset` manage EKB-owned development state; rebuilds are guarded to pre-v1.
`scan` ingests/resumes; concepts, lineage and sources expose shared services.
`architectures register <manifest.yaml> <artifact>` stores an immutable solution-architecture
revision; list/show inspect it. `baselines register` retains a baseline and only proposes
canonical adoption for human decision. `assess` verifies a solution against the approved baseline.
`pipeline` runs resumable workflows with progress and status snapshots; `workflow.py` registers
orchestration and review commands. Resume accepts an explicit profile so model capabilities do not
silently change after an interrupted run.
Connection refusals name the expected PostgreSQL address and safe startup/configuration guidance.
`scans list/tasks/usage` exposes persisted failure and usage diagnostics; task pages use `--after`.
Unknown or incomplete prices remain null, including after a task retry.
`eval skills/list/history` uses bounded Pydantic Evals cases and durable skill history.
Live evaluation requires opt-in; `--compare-concurrency` additionally requires `--live`.
Optional per-deployment request/token limits are shared across cases and comparison arms.
`eval goals/documents/nist` checks source-grounded strategic, mixed-layer and framework cases.
`eval provenance` accepts an explicit runtime profile and `--split all` for complete-corpus
qualification without silently truncating the evaluation below its 1,000-case bound.
`eval report --latest` displays the newest retained strategic-goal Markdown report.
`eval qualify/verify-corpus/distill` supports model gates, provenance and train-only export.
`test --ui` adds frontend build and local Playwright checks to Python tests/evaluations.
`tui`, `web`, `mcp` and `worker` expose the other process interfaces.
Async use cases enter `Runtime`; keep `asyncio.run` at this synchronous process boundary.
Use `docs\usage.md` and `uv run ekb --help` for command examples; keep handlers thin.
