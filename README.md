# Enterprise Knowledge Base (EKB)

An executable Python 3.13 modular monolith for incremental, evidence-grounded objectives and
requirements. PostgreSQL retains immutable revisions, exact source evidence and proposed
relationships. Filesystem or Azure Blob artifacts preserve originals. CLI, Textual, web and
read-only Model Context Protocol (MCP) interfaces share the same application services.

**Default inference is an explicitly limited offline rules baseline.** It requires no cloud
credentials and makes no paid calls. Configure a real model before assessing model quality.
Candidates and source wording are not approved enterprise policy or a compliance conclusion.

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

The manifest includes Shell's 2025 annual report (selected PDF pages), public strategy and
sustainability pages, selected official NIST CSF 2.0 outcomes and SP 800-53 controls, and precisely
identified UK/US offshore safety publications. Each entry documents publisher, identifier,
format, jurisdiction, access conditions and intended scope. Full downloaded originals are retained;
only selected NIST controls/annual-report pages are initially parsed.

ISO/IEC 27001 and ISO 14001 entries are **metadata references only**. No ISO full text or invented
ISO clauses are provided. Put suitably licensed documents in an authorized private source directory.
Regulatory entries do not assert applicability to Shell or every oil-and-gas operation. Check current
law, amendments, geography and enterprise adoption before using a candidate as an obligation.

Public sites can move or deny automated requests. Fetch failures remain visible and do not erase
historical evidence. Original content hashes preserve the retrieved edition; mutable upstream URLs
are not a substitute for an archival citation.

### Downloadable parser-test corpus

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

```powershell
uv run ekb tui --directory tests\fixtures\local
# Or: uv run ekb tui --manifest sources\public.yaml

$env:EKB_SOURCE_DIRECTORY = "C:\documents"
uv run ekb web
# Open http://127.0.0.1:8000

uv run ekb mcp
```

The TUI supports source inventory, scan/resume, per-source progress, failures, concepts,
provenance, relationship inspection and usage statistics. The server-rendered Jinja2 web interface
provides the same inspection workflows with vendored HTMX and no Node.js build. API documentation
is at `/docs`; `/health` checks PostgreSQL connectivity.

The web scan button uses the server-configured `EKB_SOURCE_DIRECTORY` and/or the public manifest.
It does not accept arbitrary filesystem paths from a browser. Keep the web server local/private:
enterprise authentication and network hardening are not part of this milestone.

The MCP server uses stdio and exposes only `search_concepts`, `get_concept`, `trace_provenance`
and `find_related_requirements`, with explicit schemas. Example client process configuration:

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

No model is advertised as optimal without a real evaluation. Embeddings and separate expensive
deduplication/relationship model passes are deliberately deferred.

## Tests and evaluation

Offline unit tests and the small golden evaluation need no database or cloud credentials:

```powershell
uv run pytest -m "not integration" -q
uv run ekb eval run --baseline --output data\evaluation-rules.json
```

Evaluation produces JSON predictions, metrics, token/cost data and per-document measured latency,
plus a concise `.txt` summary. The golden corpus distinguishes short attributed public excerpts
from synthetic cases and contains no licensed ISO clauses. It scores kind precision/recall,
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
uv run alembic check
```

Integration/contract tests verify unchanged scans, selective revisions, exact provenance, restart,
cancellation, concurrent claims, ownership fencing, immutable version/authority history, cyclic
lineage, PDF/Word/PowerPoint directory ingestion, and web/TUI/MCP inspection. Azure Blob, model
inference and plugin contracts are tested without cloud credentials. Without
`EKB_TEST_DATABASE_URL`, PostgreSQL-dependent tests are explicitly skipped.

## Telemetry and debugging

Application logs and traces are JSONL in `data\telemetry`, correlated by trace/span IDs.
`EKB_LOG_MAX_BYTES` and `EKB_LOG_BACKUPS` control rotation. Source text, raw SQL parameters,
authorization headers and exception messages are not emitted by application spans.

`EKB_DEBUG_ARTIFACTS=true` writes model input/output separately under `data\debug`, including
trace references for diagnosis and replay. This can contain confidential content: restrict its
Windows ACLs and retention. It is off by default and never includes authorization headers.

For Azure exporters, install `uv sync --extra azure`, set
`EKB_AZURE_MONITOR_CONNECTION_STRING`, and select `config\azure.yaml`. The same application spans
and correlated logs then also export to Application Insights. No live Azure export or managed
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
container, assign narrowly scoped permissions, configure secure PostgreSQL access, run migrations,
and deploy the web/worker with the same settings. Full infrastructure provisioning, public endpoint
authentication, private networking, secret rotation and deployment automation are intentionally
deferred. Do not expose this MVP as an unauthenticated enterprise service.

## Design and limitations

See `docs\architecture.md` for implemented architecture and design, `docs\plugins.md` for extension
contracts, and `docs\decisions` for architectural choices. The seven documents in `docs\codebase`
map architecture, stack, structure, conventions, integrations, tests and limits back to source
evidence. Start with the nearest folder README for local orientation. `AGENTS.md` requires concise
folder guides to stay current, with a pytest guard covering new folders as well.
Domain and port modules are strictly type-checked and do not import provider/infrastructure SDKs.

The offline baseline misses implicit objectives, does not extract scope, and can misclassify CSF
outcomes. PDF column/heading reconstruction remains heuristic; diagrams are retained as references,
not semantically interpreted. Canonical promotion, automatic version classification, semantic merges,
exception management, SharePoint, ArchiMate, Bizzdesign and Neo4j adapters are not implemented.
The MVP prioritizes correct provenance and recoverable incremental processing.
