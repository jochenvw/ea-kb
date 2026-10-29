# Enterprise Knowledge Base (EKB)

An enterprise architecture knowledge tool for extracting objective and requirement candidates while
preserving original documents, exact source evidence and revision history. Incremental scans resume
interrupted work and skip unchanged inputs. Built with async Python 3.13, PostgreSQL and replaceable
adapters.

Inspect results through the command line, terminal UI, shadcn/ui browser workspace or read-only
Model Context Protocol (MCP) server.
Durable indexing, enrichment and issue stages support audited reconciliation and strict scoped
canonical resolution without automatically approving candidates.

## Quick start

Requires Python 3.13, [uv](https://docs.astral.sh/uv/), Git and Docker with a running Linux engine.
From a checkout, in PowerShell:

```powershell
uv sync
docker compose up -d postgres --wait
uv run ekb init
uv run ekb scan tests\fixtures\local
$env:EKB_SOURCE_DIRECTORY = "tests\fixtures\local"
uv run ekb web
```

Open <http://127.0.0.1:8000>. The fixture documents are synthetic.
Browse knowledge, source evidence, the review queue and pipeline activity. No Node runtime is needed.
Use `uv run ekb --help` for other commands.

## Documentation

- [Usage and operations](docs/usage.md): configuration, corpus downloads, models, tests and deployment.
- [Architecture](docs/architecture.md) and [plugin contracts](docs/plugins.md).
- [Reconciliation, pipeline commands and actual-corpus demonstration](docs/phase3.md).
- [Latest ingestion verification and results browser](docs/ingestion-verification.md).
- [Teacher datasets, frugal model qualification and unified tests](docs/evaluation.md).
  `uv run ekb test --ui` combines Python/browser tests and offline evals (UI checks need Node).
  [Foundry Bicep](infra/README.md)
  provisions configurable student/embedding models with no teacher deployment by default.
- [Codebase map](docs/codebase/README.md) and [agent coding standards](AGENTS.md).
  Short folder READMEs explain local responsibilities and entry points.

**Local/private MVP:** token-protected HTTP governance, no enterprise identity or access roles.
Extraction defaults to limited offline rules
with no paid calls; configure a model before assessing model quality. Candidates are not approved
policy or compliance conclusions. Live model inference and Azure deployment remain unverified.
