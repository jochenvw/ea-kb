# Command-line entry point

`app` in `__init__.py` is registered as the `ekb` console script by `pyproject.toml`.

- `init` creates the development schema; `--recreate` rebuilds owned tables/caches before v1 only.
- From v1, `init` uses migrations; `scan` ingests/resumes; inspection reads persisted knowledge.
- `reset` clears guarded EKB tables/caches without re-indexing; `--yes` bypasses confirmation.
- `concepts phrase` caches concise assertions using the configured model; `--source-id` restricts inputs.
- `concepts list --level implementation` separates platform requirements from goals/guidance.
- `sources sync microsoft-security-benchmark` downloads and normalizes actual published assets.
- `compare` persists review-only comparisons; `lineage` accepts UUIDs or `mcsb-v2:NS-6`.
- `workflow.py` adds `pipeline run/status/resume`, `reconcile`, `issues` and strict `knowledge` groups.
- `pipeline run` supports source/profile selection, guarded `--fresh`, `--json` and saved reports.
- `presentation.py` renders ASCII branding, file/check progress, accurate summaries and safe links.
- `eval workflow` demonstrates retained real sources and explicitly simulated isolated governance.
- `evaluation.py` adds `test`, `eval qualify/verify-corpus/distill/skills`; live calls require opt-in.
- `eval goals` source-audits annotations and compares strategies; provider calls require `--live`.
- `eval documents` diagnoses company coverage/mixed layers; `--verify-sources` checks retained originals.
- `eval nist` scores native Core extraction; `--excerpts-only` replays sources, `--predictions` scores exports.
- `eval report --latest` displays the newest retained strategic-goal Markdown report.
- `test --ui` includes frontend checks/build and local Playwright regressions before Python/evals.
- `eval`, `doctor`, `tui`, `web`, `mcp` and `worker` expose evaluation, diagnostics and process launch.
- Async use cases enter `Runtime`; keep `asyncio.run` at this synchronous process boundary.

Use `docs\usage.md` command examples and `uv run ekb --help`.
Do not move ingestion/business rules into Typer handlers; share the existing services.
