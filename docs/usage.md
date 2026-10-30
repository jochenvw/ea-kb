# Usage and operations

Run commands from the repository root. See the [project overview](../README.md) for a quick start.

An executable Python 3.13 modular monolith for incremental, evidence-grounded strategic goals and
requirements. PostgreSQL retains immutable revisions, exact source evidence and proposed
relationships. Filesystem or Azure Blob artifacts preserve originals. CLI, Textual, web and
read-only Model Context Protocol (MCP) interfaces share the same application services.
Ingestion runs and monitoring are CLI-only, including the CLI-launched periodic worker; HTTP APIs
and the TUI do not start or monitor ingestion. Governance review is separate.

**Default inference is an explicitly limited offline rules baseline.** It requires no cloud
credentials and makes no paid calls. Configure a real model before assessing model quality.
Candidates and source wording are not approved enterprise policy or a compliance conclusion.

`uv run ekb test` combines code tests and source-grounded offline evals; the full profile requires
`EKB_TEST_DATABASE_URL`. See [evaluation and distillation](evaluation.md) for teacher datasets,
train-only exports, live quality/cost gates and [minimal Foundry infrastructure](../infra/README.md).

## Quick start (PowerShell)

Prerequisites: Python 3.13, [uv](https://docs.astral.sh/uv/), Docker with a running Linux engine.

```powershell
uv sync
docker compose up -d postgres --wait
uv run ekb init
uv run ekb doctor
uv run ekb scan tests\fixtures\local
uv run ekb scan tests\fixtures\local
uv run ekb concepts list
```

The second scan skips unchanged sources without parsing or extraction. The local fixture is
explicitly synthetic; it is not an external publication or approved policy.
All non-symlink local files are inventoried; unsupported formats remain metadata-only and never
enter parsing/inference. Inventory/acquisition completes before semantic processing starts.

Scan a private directory containing PDF, DOCX, PPTX, TXT, Markdown or OSCAL JSON:

```powershell
uv run ekb scan C:\documents --dry-run
uv run ekb scan C:\documents
uv run ekb sources list
uv run ekb scans list
uv run ekb concepts show CONCEPT_UUID
uv run ekb lineage CONCEPT_UUID
```

`--dry-run` performs discovery and database reads only: no downloads, artifact writes, parsing,
inference or durable scan records. Its `processed` count means sources that would need checking;
HTTP changes cannot be established without a request. Run the same command after interruption to
resume checkpoints. A crashed worker's lease becomes reclaimable after five minutes.

Copy `.env.example` to `.env` for overrides. Runtime data lives under gitignored `data`.
`EKB_CONFIG_FILE` selects a YAML profile; explicit settings, environment and `.env` override YAML.

### One-command ingestion

```powershell
uv run ekb pipeline run --directory tests\fixtures\local
uv run ekb pipeline run --manifest sources\public.yaml
# Or use source/stage settings from a profile:
uv run ekb pipeline run --config config\local.yaml
# Explicitly destructive to EKB database state; originals are retained:
uv run ekb pipeline run --config config\local.yaml --fresh
# Automation only: confirmation is required when fresh JSON mode is selected:
uv run ekb pipeline run --config config\local.yaml --fresh --yes --json
```

The ASCII banner precedes the selected YAML source definitions and worker limits. The three
visible passes are:

1. **Acquire:** bounded parallel discovery/revision checking and downloads. Lists each file as
   known/not-known to the DB, reused, downloaded or metadata-only, then summarizes counts.
   Publisher documents use the same explicitly configured connectors. Linked pages are not
   downloaded unless individually configured; semantic extraction has not begun.
2. **Label and extract:** after acquisition completes, bounded workers open prepared originals,
   reuse parsing, independently checkpoint ontology/scope labels, then extract only within those
   labels. Exact evidence, qualifiers and scope are validated before candidates persist. An ephemeral
   disk-spooled inventory avoids keeping downloaded documents or an unbounded job list in RAM.
   The summary distinguishes processed/skipped files and emitted candidate results (not unique concepts).
3. **Reconcile and detect issues:** the existing durable enrichment and issue stages run in order.
   Each reports new concepts/proposals/issues and executed/reused checks; decisions remain governed.
   Independent files run concurrently, but dependent stages/checks are not blindly parallelized.

The run ID is printed as soon as the durable run starts. Stage summaries and final check outcomes
include statuses and identifiers. The overall summary includes persisted totals, observed workspace
changes, optional UI links, a credential-free database location and a JSON report under
`data\reports`. `--output FILE` selects a report path; `--json` suppresses human progress.
Counts are not claims of approval or compliance.
Failures produce a nonzero exit, retain checkpoints and save available failure details. UI links
are optional result inspection and assume `uv run ekb web` at the default address.

In another terminal, with the same database, data directory and model/profile settings:

```powershell
uv run ekb pipeline status                 # latest persisted snapshot, not a live subscription
uv run ekb pipeline status RUN_UUID
uv run ekb scans list                      # recent scan counts and source/error identifiers
uv run ekb scans tasks --limit 100         # bounded task errors, attempts and individual usage
uv run ekb scans tasks --after LAST_TASK_UUID
uv run ekb scans usage                     # bounded workspace usage, not a per-run total
uv run ekb pipeline resume RUN_UUID
```

Set `EKB_CONFIG_FILE` in both terminals when using a nondefault profile; `--config` selects only the
invocation on which it appears. Status includes run, stage and check IDs, attempts and error types.
After interruption, resume the same run rather than resetting it. The pipeline's renewable lease
prevents concurrent owners. Each non-dry-run scan, including a standalone scan before it claims
any tasks, also has a renewable, fenced lease. Crashed pipeline and scan leases expire after up to
120 seconds; an individual processing-task lease can take five minutes. A pipeline resume fails
rather than declaring success if a required task is still leased; `ekb scans list` identifies
`ProcessingIncompleteError`. Retry the same run after that task finishes or its lease expires.
`ekb scan` and `ekb worker --directory PATH --once` remain available for indexing-only operation.

Task pages cover at most 1,000 tasks in identifier order; pass the last ID as `--after` for the
next page. Usage summarizes the first 1,000 tasks and marks `tasks_truncated` if more exist;
both may include earlier runs. Usage reports committed tokens, model calls, latency and validation
failures. Aggregate `configured_cost` is null if any included task lacks a price; an explicitly
priced zero stays zero.
This is not an invoice: failed/uncommitted calls can still incur charges. Older task records written
before the usage-accounting fix may lack model-call counts or contain partial priced subtotals;
token-bearing tasks with missing call counts make aggregate calls and cost null. Other missing
historical information cannot be reconstructed, so do not treat old task amounts as complete.

`--fresh` clears **only EKB database state**, including processing and workflow checkpoints.
It retains filesystem/Azure original artifacts, Alembic state, unrelated tables, source files and
manifests, reports and telemetry, including previously downloaded source-input directories.
The four obsolete `benchmark_documents`, `benchmark_comparisons`, `benchmark_embeddings` and
`benchmark_match_cache` tables and legacy `concept_aliases` are removed only in the active schema,
both for reset and recreation. Cleanup does not use `CASCADE`: a dependency from an unrelated table
fails and rolls back the reset. Active scans/leases block the operation. Stop ingestion workers first;
a read-only browser may remain open.
Do not use `--fresh` merely to retry failed work; ordinary reruns reuse valid checkpoints.
A hard crash can leave the last persisted scan report marked `running`; it is not rewritten with an
invented finish time. Reset ignores that report only after its scan guard has expired, and still
refuses any active scan, pipeline or task lease. Its liveness record is the `pipeline_leases` row
named `ingestion-scan:<scan UUID>`, with expiry measured by the PostgreSQL clock. A `running` report
from an older version without a scan guard remains conservatively blocking: its liveness cannot be
proved from the report alone. Lease loss fences database commits, but an already-running filesystem
write may finish afterward and leave an unreferenced content-addressed artifact; it cannot establish
a source revision without a valid guard. Shared Azure blobs are never cleared by this reset.

To clear indexed knowledge and runs **without re-indexing**, use
`uv run ekb reset` (confirmation) or `uv run ekb reset --yes` (automation). The same safety
boundaries and retained source inputs apply.

Application **0.1.0** is pre-release development: before major version 1, backwards compatibility and
data-preserving migrations are not required. After schema changes, run `uv run ekb init --recreate`
to drop/recreate only EKB-owned tables. Original artifacts remain; no re-indexing starts.
Previously downloaded source inputs, including the former security-guidance corpus directory, are
also retained; provide a directory explicitly to scan those files with ordinary connectors.
`ekb init` without that flag creates missing tables; normal web startup never rebuilds anything.
From v1, `--recreate` is disabled and schema changes require migrations. See decision `0009`.

### Foundry models with local storage

`config\foundry.yaml` opts into paid extraction/reconciliation (`ekb-student`) and embeddings
(`ekb-embedding`) without moving PostgreSQL or artifacts to Azure. Authenticate with `az login`,
deploy those names in your existing account, and select its **OpenAI v1** endpoint:

```powershell
uv sync --extra agent-framework
$env:EKB_MODEL_ENDPOINT = "https://YOUR-ACCOUNT.openai.azure.com/openai/v1"
uv run --extra agent-framework ekb pipeline run --config config\foundry.yaml
```

The profile requires HTTPS/Entra authentication, structured output and completion-token parameters.
It uses Microsoft Agent Framework for the two bounded ingestion skills. Optionally select a
cheaper labeling deployment with `EKB_LABELING_MODEL_ID`; blank uses `EKB_MODEL_ID` for both.
This override shares the endpoint/capability limits but needs its own labeling token prices.
Use `ekb eval skills --skill labeling` / `--skill extraction` to evaluate each independently.
Environment/`.env` overrides still apply; check the printed provider/model IDs before starting.
Keep account-specific profiles under ignored `data`, never commit tokens or keys. An existing
deployment and a successful smoke test prove access, not quality: run the held-out qualification
workflow in `evaluation.md` before calling these models qualified. Cost remains unknown until
actual input/output/embedding prices are configured.

### Package feeds

The lock file was resolved using the approved `packagefeedproxy.microsoft.io` PyPI mirror.
If public wheel access is blocked, set the approved feed for build-system dependencies too:

```powershell
$env:UV_DEFAULT_INDEX = "https://packagefeedproxy.microsoft.io/pypi/simple"
uv sync
```

The container uses that mirror by default and accepts `--build-arg UV_DEFAULT_INDEX=...`.
In an environment without access to the mirror, intentionally regenerate the lock against an
authorized reachable index; do not disable certificate validation.

## Official public corpus

```powershell
uv run ekb scan --manifest sources\public.yaml
```

The sample manifest combines direct Microsoft security guidance pages, a public corporate annual report
(selected PDF pages), strategy and sustainability pages, selected official NIST CSF 2.0 outcomes and
SP 800-53 controls, and precisely
identified UK/US offshore safety publications. These are illustrative sources, not an industry or
company restriction. Each entry documents publisher, identifier,
format, jurisdiction, access conditions and intended scope. Full downloaded originals are retained;
only selected NIST controls/annual-report pages are initially parsed.

ISO/IEC 27001 and ISO 14001 entries are **metadata references only**. No ISO full text or invented
ISO clauses are provided. Put suitably licensed documents in an authorized private source directory.
Regulatory entries do not assert applicability to any particular enterprise or operation. Check current
law, amendments, geography and enterprise adoption before using a candidate as an obligation.

Public sites can move or deny automated requests. Fetch failures remain visible and do not erase
historical evidence. Original content hashes preserve the retrieved edition; mutable upstream URLs
are not a substitute for an archival citation.

### Security guidance as ordinary sources

The manifest includes Microsoft Azure Security Benchmark and Microsoft Cloud Security Benchmark
overview pages **and** separately configured network-security articles. An overview does not
automatically fetch its links. Each document uses the HTTP connector, standard parser and shared
ontology skills, retaining the actual URL, edition, publisher and CC-BY 4.0 attribution.

```powershell
uv run ekb scan --manifest sources\public.yaml
uv run ekb concepts search "web application firewall"
uv run ekb lineage CONCEPT_UUID
```

The offline rules baseline is deliberately conservative and does not establish requirement layers.
An explicitly configured model can propose a conceptual, technical or platform requirement only
with matching evidence; unknown labels stay null and explicit Azure/product scope is retained.
Publisher mappings do not imply equivalence, compliance or enterprise adoption, and unresolved
targets are not invented. Publication maturity and scoped authority remain independent.

Optional generic semantic verification and embeddings use `EKB_RECONCILIATION_MODEL_ID` and
`EKB_EMBEDDING_MODEL_ID`; both are disabled by default and can incur charges when enabled.
Completed checks replay without repeating work; changed checks may make new provider calls.
Offline tests do not qualify a live model.

### Additional downloadable parser-test corpus

The supplied corpus downloader retrieves additional real PDFs, a Word assessment template,
the complete NIST OSCAL catalog, and public overview/regulatory HTML when publishers permit it:

```powershell
uv run python scripts\download_test_corpus.py --dry-run
uv run python scripts\download_test_corpus.py
```

Its source list is `config\test_corpus.yaml`; downloaded files and a SHA-256/retrieval index stay
under gitignored `data\test-corpus\public`. Repeat runs validate cached files and use conditional
requests when available; `--force` refreshes them. This is a download-only parser-test collection,
separate from the selected ingestion manifest above. It makes no model calls. ISO remains public
overview metadata, never licensed standard text. Publisher refusals or empty challenge responses
are recorded in `index.json` under `failures` and cause a nonzero exit; valid existing files survive.

## Inspection interfaces

The browser separates looking things up from doing governed work:

| Area | Pages |
|---|---|
| Reference | `/sources` and `/sources/{id}`: source inventory, extracted versions and exact evidence |
| Reference | `/knowledge`, `/concepts/{id}`, `/explore`: interpretations, provenance and visual navigation |
| Reference | `/reconciliation`: proposal ledger and decision history |
| Reference | `/ontology`: read-only OWL concepts, relations and exact definitions |
| Work | `/review`: active reconciliation backlog and issue triage |
| Operations | `/data`: read-only EKB tables and foreign-key drill-down; `/logs`: recent application events |

Source detail links preserve the evidenced version via `?version_id=...`. Graphs navigate
concept-level source history and stored/proposed links across versions, not automatic equivalence
or evidence for every version; dashed proposals remain distinct from asserted relationships.
The graph is a bounded neighborhood (12 connected interpretations, 8 source documents), with
click-through navigation rather than an unbounded whole-corpus visualization.

The reconciliation backlog includes manual and latest-successful-enrichment proposals without an
accepted/rejected decision head. Deferred and reversed decisions remain outstanding; obsolete-run
proposals stay in the reference ledger, not the work queue. Issues are a separate severity-ordered
triage view. Decision forms still require configured operator authentication and explicit rationale.

`/data` permits only registered EKB tables, at most 50 records per primary-key page, exact-record
lookup and linked foreign keys. Text/nested JSON/arrays are previews; this is not a full export,
arbitrary SQL console or database editor. Unrelated database tables and configuration secrets
are not exposed. Keep the existing local/private deployment boundary.
`/logs` shows up to 100 recent events by default (API maximum 200), from the newest 16 among at most
256 discovered log-file candidates, with 64 KiB read per file. Only known application events and
diagnostic fields are served; raw exception messages, traces and model payloads are excluded.
Use **Refresh logs** to reread the tails; older files remain on disk.

```powershell
uv run ekb tui

uv run ekb web
# Open http://127.0.0.1:8000

uv run ekb mcp
```

The TUI supports source inventory, concepts, provenance, relationship inspection and review.
It does not execute or monitor ingestion. The React/shadcn browser workspace targets
desktop/PC use, not mobile. It provides
a consolidation workspace (`/`), shared language (`/ontology`), cursor-paged interpretations
(`/knowledge`), source curation (`/sources`), visual validation (`/explore`), issue/proposal review
(`/review`) and read-only data/log inspection (`/data`, `/logs`).
Start with the shared definitions, then inspect source context, interpretations, relationships and
scoped decisions. The four-step path is navigation guidance, not saved progress or approval.
Source selection stays in operator configuration; source curation checks do not change source status.
Concept deep links retain exact evidence, version history and scoped authority. The default
**Source evidence** tab shows only the selected version's passages. **Visual validation** is separate
from **Scope & governance**; recorded authority and adoption proposal defaults follow the selected
version. A graph is a bounded concept neighborhood across retained versions, not evidence for a
specific version or a global standards-coverage map. API documentation
is at `/docs`; `/health` checks PostgreSQL connectivity. Built assets are included in the Python
package: normal operation needs neither Node.js nor a separate frontend server.

For frontend development, use Node.js 22.12+ and run these commands inside `frontend`:

```powershell
npm ci
npx playwright install chromium --only-shell
npm run dev
# Or rebuild committed production assets:
npm run build
```

The optional Vite development server proxies the local backend on port 8000. `npm run verify`
checks formatting, type-checks/builds and runs synthetic Playwright browser regressions.
`uv run ekb test --ui` from the repository root includes those checks with Python tests and evals.
Use the Microsoft npm upstream in `frontend\.npmrc`; see `frontend\scripts\README.md` for lock updates.
Do not hand-edit generated browser assets; the Python suite detects stale source fingerprints.
Theme selection follows the OS, can be switched in the header and persists locally.
`?scoutTheme=light` or `?scoutTheme=dark` explicitly selects the initial theme.
The [Bold Wikipedia preset](https://tweakcn.com/r/themes/cmlmpb3qp000004l5go47hzsv) supplies
both palettes, compact corners, typography and shadows. Inter is self-hosted with the application;
no font/CDN requests are needed. Semantic shadcn variables also drive the custom reference views.

The source registry lists individual original documents through `/api/sources`. The knowledge
table filters by **Source**; API/CLI search `source_id` / `--source-id` accept the original source ID.
Each row names its source and opens the version actually evidenced there; filters persist
in the URL and reset paging. Details show the **Original quote**, highlighted surrounding source
context, section/page and revision metadata. Quotes and context are rendered as text, never HTML.
When a quote spans its whole segment, adjacent retained passages provide context where available;
their section headings are shown explicitly. Missing context is disclosed, never reconstructed.

**Shared language** under Workspace (`/ontology`, read-only API `/api/ontology`) shows the canonical concepts,
semantic relations, classification/scope properties and exact definitions from the committed OWL/RDF.
Search names/definitions, inspect domain/range, symmetric/transitive characteristics, IRIs and version.
Concepts are grouped by company intent, technology-independent requirements, platform requirements
and classification context; these presentation groups imply no subclass or support relationships.
Interpretations display their canonical definition inline. Knowledge/source badges link to the
same catalog; unknown types say
**Unclassified**. The catalog is available even when no documents have been indexed.
Open **Show Mermaid diagram** to visualize the complete ontology and inspect its generated Mermaid
source. Boxes are classes and property types, not enterprise-specific assertions. Only OWL-declared
domains and ranges are drawn as arrows; semantic properties without declared constraints remain
unconnected. Search filters the catalog, not the diagram. Rendering is local and read-only.

**Architecture level** defaults to **Goals & guidance**: strategic goals and requirements not classified
as `PlatformRequirement`, including unclassified native guidance. Select **Platform implementation**
for explicitly platform-scoped `PlatformRequirement` records, or **All levels** to compare.
Level, text and source filters select the same version; CLI/API also accept `--level implementation`
and `level=implementation`. Unknown scope remains unknown, not universal applicability.

Concept details and the visual explorer show goals/guidance above implementation. Click a source
for evidence or a connected interpretation to recenter. No company-goal alignment, adoption,
equivalence or compliance is inferred. Review proposals are hidden by default;
**Show review proposals** reveals them as dashed links. Unresolved links are not drawn.

Assertions use one standalone sentence: **supported subject + action/outcome + necessary qualifiers**.
Aim for 8–25 words; preserve intent, scope, negation, exceptions, numerical targets, units, deadlines
and original must/should/goal language. Do not invent a subject or turn a publisher's aim into policy.
If the evidence cannot support clear phrasing, the UI labels the original wording for clarification.
Rephrasing is a derived candidate cache, not approval or a rewrite of original evidence.

After ingestion, explicitly run the configured model to phrase existing versions:

```powershell
uv run ekb init
$env:EKB_CONFIG_FILE=(Resolve-Path data\foundry-local.yaml).Path
uv run ekb concepts phrase --concurrency 4
# Optional: add --source-id <source UUID> to phrase only one document.
```

The command is resumable: unchanged model/prompt/input fingerprints reuse phrasing without model
calls. It reports generated, unresolved, reused and failed versions; nonzero failures return exit 1.
Already concise, standalone modal assertions retain their exact wording without a model call.
Rules-only inference cannot semantically rephrase and is rejected rather than silently used.
The nullable assertion cache records provider-reported usage; unpriced costs remain unknown.

The web application does not start, monitor or own ingestion runs. Non-health web/API routes reject
non-loopback clients and forwarded-proxy headers. Enterprise authentication and network hardening
are not implemented; do not expose this service through a proxy.

The MCP server uses stdio and exposes exactly eight read-only tools: `search_concepts`, `get_concept`,
`trace_provenance`, `find_related_requirements`, `resolve_knowledge`, `resolve_requirement`,
`list_knowledge_issues` and `get_reconciliation_history`, each with an explicit schema. Example
client process configuration:

```json
{
  "mcpServers": {
    "ekb": {
      "command": "uv",
      "args": ["--directory", "C:\\_CODE\\ea-kb", "run", "ekb", "mcp"]
    }
  }
}
```

Use your checkout path. Stdout is reserved for the MCP protocol; application logs go to disk.

## Model providers

For Foundry Local or another OpenAI-compatible server:

```powershell
$env:EKB_INFERENCE = "openai"
$env:EKB_MODEL_ENDPOINT = "http://localhost:5272/v1"
$env:EKB_MODEL_ID = "YOUR_ACTUAL_SERVED_MODEL_ID"
$env:EKB_MODEL_STRUCTURED_OUTPUT = "false"
uv run ekb scan C:\documents
```

For Microsoft Foundry, use the actual deployed model name and its supported OpenAI-compatible
base endpoint (typically ending in `/openai/v1`). Configure `EKB_MODEL_API_KEY` **or**
`EKB_MODEL_MANAGED_IDENTITY=true`. Never place credentials in the manifest or source code.

Enable `EKB_MODEL_STRUCTURED_OUTPUT` only for a deployment that supports JSON-schema output.
Otherwise schema instructions plus Pydantic validation are used. Exact grounding is always checked.
Validation retries are bounded by `EKB_MODEL_RETRIES`; transient transport failures use exponential
backoff and jitter. No autonomous tool loops.

Configure actual context limits and adjust `EKB_SEGMENT_MAX_CHARS` /
`EKB_MODEL_MAX_OUTPUT_TOKENS` for smaller deployments. Oversized requests fail instead of silently
truncating evidence. `EKB_MODEL_INPUT_PRICE` and `EKB_MODEL_OUTPUT_PRICE` are optional prices per
million tokens; without them cost remains unknown.

The optional Microsoft Agent Framework adapter uses the verified chat client API:

```powershell
uv sync --extra agent-framework
$env:EKB_INFERENCE = "agent-framework"
# Set the same endpoint, actual model ID and credentials/capabilities as above.
```

No model is advertised as optimal without a real evaluation. Optional reconciliation/embedding
providers also serve bounded generic enrichment; both remain disabled by default.
See `phase3.md` for durable pipeline configuration, review and strict canonical queries.

## Tests and evaluation

Offline unit tests and the small golden evaluation need no database or cloud credentials:

```powershell
uv run pytest -m "not integration" -q
uv run ekb eval run --baseline --output data\evaluation-rules.json
```

Evaluation produces JSON predictions, metrics, token/cost data and per-document measured latency,
plus a concise `.txt` summary. The golden corpus distinguishes short attributed public excerpts
from synthetic cases and contains no licensed ISO clauses. It scores ontology-type precision/recall,
modality, scope (including known scope separately), exact evidence and duplicate pairs.
Invented mandatory requirements receive a five-times additional penalty. Negations/exceptions
cannot be omitted from a matched statement. These checks do not prove semantic entailment.

To evaluate a configured model, run without `--baseline`, saving a separate output. To compare a
small model with a stronger baseline, run both against the same dataset hash with their real
endpoints/model IDs. No benchmark or comparison is fabricated when access is unavailable.

The complete verification loop uses disposable schemas in the local PostgreSQL service:

```powershell
uv sync --all-extras
$env:EKB_TEST_DATABASE_URL = "postgresql+asyncpg://ekb:ekb@localhost:5432/ekb"
uv run pytest -q
uv run ruff check src tests migrations scripts
uv run ruff format --check src tests migrations scripts
uv run basedpyright
```

Before v1, persistence tests recreate disposable schemas from current ORM metadata.
Use `uv run ekb init --recreate` for a breaking local schema change; from v1, also run
`uv run alembic check` against the migrated database.

Integration/contract tests verify unchanged scans, selective revisions, exact provenance, restart,
cancellation, concurrent claims, ownership fencing, immutable version/authority history, cyclic
lineage, PDF/Word/PowerPoint directory ingestion, and web/TUI/MCP inspection. Azure Blob, model
inference and plugin contracts are tested without cloud credentials. Without
`EKB_TEST_DATABASE_URL`, PostgreSQL-dependent tests are explicitly skipped.

## Telemetry and debugging

Application logs, traces and metrics are JSONL in `data\telemetry`, correlated by trace/span IDs.
`EKB_LOG_MAX_BYTES` and `EKB_LOG_BACKUPS` control rotation. Source text, raw SQL parameters,
authorization headers and exception messages are not emitted by application spans.

`EKB_DEBUG_ARTIFACTS=true` writes model input/output separately under `data\debug`, including
trace references for diagnosis and replay. This can contain confidential content: restrict its
Windows ACLs and retention. It is off by default and never includes authorization headers.

For Azure exporters, install `uv sync --extra azure`, set
`EKB_AZURE_MONITOR_CONNECTION_STRING`, and select `config\azure.yaml`. The same application spans
and correlated logs/metrics then also export to Application Insights. No live Azure export or managed
identity deployment is implied by offline tests.

## Containers and Azure foundation

```powershell
docker compose up -d postgres --wait
docker compose --profile app build
docker compose --profile app run --rm web ekb init
docker compose --profile app up -d
# http://127.0.0.1:8000
docker compose --profile app logs --tail 30 web worker
```

The image includes Python 3.13, both optional provider/exporter dependencies and English Tesseract
for selective OCR. OCR is still off unless `EKB_OCR=true`. Host OCR requires its own Tesseract
language-data installation. Web and worker share a durable named artifact volume; PostgreSQL uses
its own named data volume. Container configuration defaults to offline extraction.

Keep the database and its artifact store together. Switching from host `data\artifacts` to a new
container volume or Azure Blob account requires copying the existing content-addressed artifacts;
changing a storage setting does not migrate historical originals automatically. Use a fresh database
for an independent deployment. Local PostgreSQL/web ports bind only to localhost.

The Azure foundation is the same image, with separate web and worker processes, managed-identity
blob/model access, an existing PostgreSQL database and Application Insights. See
`deploy\azure\container-apps.env.example` for environment contracts. Pre-create the private blob
container, assign narrowly scoped permissions, configure secure PostgreSQL access, initialize the schema,
and deploy the web/worker with the same settings. Full infrastructure provisioning, public endpoint
enterprise authentication, private networking, secret rotation and deployment automation are
intentionally deferred. Non-loopback requests are rejected; do not expose this MVP through a proxy.

## Design and limitations

See `docs\architecture.md` for implemented architecture and design, `docs\plugins.md` for extension
contracts, and `docs\decisions` for architectural choices. `docs\codebase\README.md` maps these
canonical guides, manifests and verification entry points. Start with the nearest folder README
for local orientation. `AGENTS.md` requires concise
folder guides to stay current, with a pytest guard covering new folders as well.
Domain and port modules are strictly type-checked and do not import provider/infrastructure SDKs.

The offline baseline misses implicit strategic goals, does not extract scope, and can misclassify CSF
outcomes. PDF column/heading reconstruction remains heuristic; diagrams are retained as references,
not semantically interpreted. Canonical promotion, automatic version classification, semantic merges,
exception management, SharePoint, ArchiMate, Bizzdesign and Neo4j adapters are not implemented.
The MVP prioritizes correct provenance and recoverable incremental processing.
