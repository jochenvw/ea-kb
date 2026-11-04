# Enterprise Knowledge Base (EKB)

**Application v0.1.0 (development).** `pyproject.toml` is the release-version source of truth.
While the major version is below 1, breaking changes and rebuilding EKB-owned data/schema are
permitted; compatibility migrations are not required. Use `uv run ekb init --recreate` after schema
changes. Normal startup does not delete data; source inputs and unrelated tables remain separate.

An enterprise architecture knowledge tool for extracting strategic-goal and requirement candidates while
preserving original documents, exact source evidence and revision history. Incremental scans resume
interrupted work and skip unchanged inputs. Built with async Python 3.13, PostgreSQL and replaceable
adapters.

Inspect results through the command line, terminal UI, shadcn/ui browser workspace or
Model Context Protocol (MCP) server (read-only apart from retaining assessment reports).
Durable indexing, enrichment and issue stages support audited reconciliation and strict scoped
canonical resolution without automatically approving candidates.

## Application goals

EKB's goal is to connect enterprise intent and approved standards to solution decisions and
implementation evidence, then use that provenance in both directions:

- Assess designs and implementations against the complete applicable baseline, including forgotten
  requirements. Distinguish permitted alternatives, approved exceptions, violations and unknowns.
- Expose accumulating exception risk through scoped counts, rates and trends, with affected goals,
  requirements and solutions visible rather than judging each exception in isolation.
- Identify recurring deviations that warrant reviewing a standard or its implementation guidance,
  without automatically weakening policy or treating every deviation as a defective standard.
- Trace proposed or adopted standard changes to affected solutions, repositories and responsible
  teams so they can review and plan updates.

`ekb assess` verifies a design and ARM/Bicep template against an explicitly approved baseline,
reporting omissions, alternatives, exceptions and unknowns; `ekb portfolio` derives exception risk
and change impact. Verification covers supported template checks only; runtime state and checks
without a verifier stay unknown.
See [goal boundaries and current status](docs/architecture.md#application-goals-and-feedback-loop).

## End-to-end flow

1. **Ingest** the configured public corpus: `uv run ekb pipeline run --manifest sources\public.yaml`.
2. **Consolidate** interpretations in the browser: sources, visual validation, reconciliation backlog.
3. **Register** a machine-readable baseline and solution design: `ekb baselines register`,
   `ekb architectures register` ([reference app](examples/reference-app/README.md)).
4. **Approve** (human only): accept baseline authority in `/review` or `ekb reconcile decide`.
   Until then every finding stays `unknown`, and unknown never passes.
5. **Assess** the template from the CLI, an MCP agent or CI: `ekb assess ... --require conforms`.
6. **Review** results: the solution page, **Portfolio risk** and the printable **Rules export**.

Step-by-step commands and expected outcomes: [agent and CI walkthrough](docs/agent-walkthrough.md).

## Quick start

**Explore populated knowledge without Foundry or model calls:**

```powershell
uv run python scripts\demo.py
```

Requires uv, Python 3.13 and Docker with its Linux engine running. Open
<http://127.0.0.1:8000>. The checked-in snapshot includes precomputed NIST catalog interpretations
and exact originals, with a separate database on port 5433; existing workspace data is untouched.
No Azure login, ingestion or inference is needed. See [offline demo and LLM instructions](docs/demo.md)
for scope, attribution, restart and maintenance. Corporate annual reports are not redistributed.

**Start an empty workspace for your own ingestion:**

Requires Python 3.13, [uv](https://docs.astral.sh/uv/), Git and Docker with a running Linux engine.
From a checkout, in PowerShell:

```powershell
uv sync
docker compose up -d postgres --wait
uv run ekb init
uv run ekb pipeline run --directory tests\fixtures\local
uv run ekb pipeline status
uv run ekb web
```

Open <http://127.0.0.1:8000>. The fixture documents are synthetic; use the full corpus below for
real results. Navigation groups: Workspace (consolidation, shared language), Consolidate (sources,
visual validation, reconciliation backlog), Knowledge (company intent, platform-agnostic, platforms
& controls, solution registry, portfolio risk), Reference (reconciliation log, rules export) and
Operations (data inspector, logs). No Node runtime is needed.
Use `uv run ekb --help` for other commands.

For the full configured corpus: `uv run ekb pipeline run --manifest sources\public.yaml`.
It prints file progress, stage/check outcomes and a retained JSON report. Add `--fresh` for a
confirmed reset of EKB-owned database state; filesystem/Azure originals and source inputs are
preserved. Ingestion and its monitoring are CLI-owned; the browser and TUI support knowledge
inspection and governance. See [operations](docs/usage.md).

## Documentation

- [Application ontology](ontology/README.md): v0.1.0-draft.3 OWL/RDF vocabulary, SHACL shapes and example.
- Read-only ontology viewer: **Workspace > Shared language** (`/ontology`); canonical definitions also at `/api/ontology`.
- [Usage and operations](docs/usage.md): configuration, corpus downloads, models, tests and deployment.
- [Agent and CI walkthrough](docs/agent-walkthrough.md): assessment via CLI, MCP, GitHub Actions and browser;
  client configs in `.vscode/mcp.json` / `.mcp.json`, CI example in [examples/ci](examples/ci/README.md).
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
policy or compliance conclusions. Live smoke tests do not establish model quality; held-out
qualification and full infrastructure deployment remain separate gates.
