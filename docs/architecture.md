# Architecture and design

EKB is an asynchronous modular monolith. PostgreSQL is the authoritative store;
original documents live in content-addressed filesystem or Azure Blob artifacts.
It is a general enterprise architecture knowledge tool: sample publishers and industries belong
in source manifests and scope metadata, not organization-specific domain logic.

## Scope and design drivers

This describes implemented ingestion, benchmarks and governed reconciliation, not every capability
requested in `goal.md` or `goal2.md`. The system extracts objective and requirement candidates while retaining
the evidence needed to inspect each interpretation. Documents, interpretations and enterprise
approval are separate concerns.

The design favors recoverable incremental processing, bounded resource use and explicit
infrastructure boundaries over exhaustive graph construction or autonomous agent execution.
`docs\codebase` contains the supporting architecture, stack, structure, conventions, integrations,
testing and concerns reference. Coding rules live in `AGENTS.md`; extension contracts live in
`docs\plugins.md`.

## Boundaries

| Layer | Responsibility | Must not import |
|---|---|---|
| `domain` | Immutable schemas, identity, scope, modality, evidence validation | SQLAlchemy, web, UI, cloud or model SDK |
| `ports` | Small typed Protocol contracts | Concrete adapters |
| `pipeline` / `services` | Ingestion, transactions, knowledge queries | ORM rows |
| `orchestration` / `stages` | Durable ordering, compatible artifacts and independent checks | Transport-specific governance |
| `adapters` | Connectors, parsing, inference, artifacts, PostgreSQL | Transport interfaces |
| `interfaces` / `frontend` | CLI, Textual, FastAPI with React/shadcn presentation, read-only MCP | Direct ORM queries (except database health checks) |
| `runtime.py` | Explicit dependency injection and resource lifetimes | Nothing implicit or global for business state |

`SourceConnector -> change detection -> ArtifactStore -> DocumentParser -> Segment ->
InferencePort -> evidence validation -> KnowledgeRepository`

Independent discovery producers feed a bounded queue. Worker count limits source downloads;
separate semaphores bound blocking parsers/OCR and inference. Blocking Office/PDF libraries run
in threads. HTTPX and SQLAlchemy sessions perform asynchronous I/O.

Benchmarks have a deterministic route: explicit synchronization discovers/downloads official
structured assets into the artifact port; normal scans read the validated cache, persist exact
segments and typed benchmark snapshots, and materialize requirements without generic parsing or AI.
Snapshots/mappings/policy definitions are records, not additional semantic primitives.

`PipelineOrchestrator` composes `indexing -> enrichment -> issues`; indexing invokes this same
ingestion engine. Versioned stage/check contracts and input/configuration fingerprints control
incrementality. A renewable 120-second lease fences concurrent runs; heartbeat failure cancels work.
Successful upstream artifacts are checked for matching contracts, source revision and producer
configuration before partial runs. Independent checks persist their own execution/cache records.
See `phase3.md` for plugin registration and the actual-corpus vertical slice.

## Components and runtime composition

| Component | Implementation | Role |
|---|---|---|
| Composition | `src\ekb\runtime.py`, `Runtime` | Select providers, own clients and create pipelines/services |
| Ingestion | `src\ekb\pipeline\__init__.py`, `IngestionPipeline` | Discover, compare revisions, claim work, parse, extract and checkpoint |
| Inspection | `src\ekb\services\knowledge.py`, `KnowledgeService` | Shared concept, provenance, relationship and usage queries |
| Contracts | `src\ekb\ports\__init__.py` | Connector, parser, inference, artifacts and unit-of-work protocols |
| Persistence | `src\ekb\adapters\persistence\postgres` | Map domain values to relational rows and execute repository transactions |
| Processing adapters | `src\ekb\adapters` | Source retrieval, parsing, inference and original-document storage |
| Transports | `src\ekb\interfaces` | Command-line, terminal, HTTP/web and MCP presentation |

`Runtime` constructs the database/session factory, artifact store, parser, inference provider and
knowledge service. Connector instances are cached per directory/manifest configuration and closed
with the runtime. CLI commands enter one asynchronous runtime; FastAPI creates one in application
lifespan; MCP does so in server lifespan. Tests can inject a runtime or knowledge service.
Migrations run explicitly through `ekb init`; web startup does not migrate the schema.

The application targets ports, but one concrete dependency remains: the pipeline imports
`SourceMissingError` from the source adapter. This is a boundary limitation, not a fully isolated
provider-independent error contract.

### Evaluations and frugal provider selection

`evaluation\dataset.py` derives versioned extraction, comparison and retrieval cases from attributed
teacher annotations, with document-family train/validation/test isolation. `distillation.py` exports
only training labels; `frugal.py` runs actual providers without sending expected answers and applies
grounding, quality, latency, token and priced-cost gates. `suite.py` combines the existing code tests
and evaluation results into one persisted report/exit status. Offline safety success is explicitly
not live model qualification; teacher labels are provisional. See `evaluation.md`.

Generic semantic enrichment unions lexical candidates with independently retrieved cosine neighbors
from a bounded assertion pool. It reuses existing PostgreSQL vector/judgment caches behind the
embedding/verifier ports; no vector database or extra semantic primitive is introduced.
`infra\main.bicep` describes minimal Entra-only Foundry model resources, with an optional teacher
disabled by default. Configuration/deployment does not establish provider quality.

## Ingestion and inspection flows

1. `ekb scan` constructs `Runtime` and connectors. Discovery yields stable identifiers, locations,
   formats, metadata and inexpensive revision indicators. Metadata-only sources become inventory
   records without downloads, source revisions or inference.
2. The pipeline reads the previous source/revision/task. Matching available-source metadata and a
   completed processing fingerprint skip work; otherwise retrieval uses local reads or conditional
   HTTP. Changed bytes are hashed and stored before a source revision is committed.
3. An atomic database claim selects one owner for a revision/fingerprint. A completed or actively
   leased task is skipped. Existing parse checkpoints are reused; otherwise the parser produces
   stable segments with page, heading, table, slide or structured-control locations.
4. Each usable segment checks extraction replay before invoking bounded inference. Results must
   satisfy schema and exact-substring grounding. Image-only segments remain references and do not
   enter text inference.
5. Ownership checks, candidate/relationship persistence, extraction checkpoints and newly incurred
   usage commit together. Task/scan records expose completion, partial failure or cancellation.
6. Inspection bypasses ingestion: an interface calls `KnowledgeService`, which opens a unit of
   work and returns domain values. Provenance joins occurrences, segments, source revisions and
   source inventory; retrieving an explanation does not require a model call.

The browser groups **Reference**, **Work** and **Operations**. The `InspectionRepository` port
returns bounded source/version pages, concept neighborhoods, active proposal queues and owned-table
previews; its PostgreSQL adapter owns all joins and cursor queries. Source drill-down selects
versions with actual occurrences in that source, not a concept's unrelated latest interpretation.
Graphs distinguish persisted relationship assertions, proposal records and source-history links;
they do not invent unresolved targets or confer version-specific authority.

The work backlog selects manual proposals plus the latest successful enrichment output, excluding
accepted/rejected decision heads and obsolete-run findings. Deferred/reversed heads remain work;
the reference ledger retains history independently. No snapshot construction, ingestion, inference
or approval occurs during inspection. Table inspection resolves names against EKB's ORM metadata,
uses primary-key cursors and foreign-key links, and exposes no SQL or write endpoint.
Log inspection runs filesystem reads off the event loop, caps file/tail counts and projects only
allowlisted operational events/fields, never raw telemetry payloads or arbitrary file paths.

Occurrence offsets delimit text in the persisted parsed segment, not byte offsets into PDF or
Office originals. Segment locations and the revision's artifact key bridge normalized text back
to the preserved document; each revision snapshots its historical source location and metadata.

The standalone `scripts\download_test_corpus.py` is a download utility, not another ingestion
service. Its `config\test_corpus.yaml` and retrieval index do not replace the pipeline's
`sources\public.yaml` manifest or PostgreSQL checkpoints.
Its `--benchmark` mode delegates to the same application synchronization connector.

## Execution and concurrency

Defaults are four source workers, a queue of eight items, two parser slots and two inference slots.
Settings can change these limits. Segments within one source are processed in order; concurrency
comes from multiple source workers. There is no external message broker: queues are process-local,
while claims/checkpoints are durable in PostgreSQL.

Transient HTTP transport failures, status 408/429 and server errors use bounded backoff with
jitter. Schema/grounding retries have a separate model limit. Permanent source failures remain
visible; only successful complete discovery establishes missing sources.

## Persistence and identity

Source identity is a deterministic UUID from its local file URI or manifest identifier.
Source revision identity adds a SHA-256 digest. Stable content-addressed artifact keys are
independent of source locations. Original source metadata/location is snapshotted per revision;
the source inventory separately reports the current location and availability.

Local artifacts are fsynced to temporary files and atomically published with create-once hard
links. Existing content is verified, never replaced. This avoids Windows rename/share-lock races
between workers. Use a local volume supporting hard links, such as NTFS or ext4.

Parsing identity includes parser implementation/library versions, OCR mode, segment size,
format and selected pages/control identifiers. Processing identity additionally includes source
content, schema, model capabilities, prompt and extraction configuration. Metadata filters avoid
unnecessary downloads where possible; SHA-256 is authoritative when bytes are fetched.
HTTP conditional requests still contact the server. No unchanged document is parsed or extracted
again when its processing fingerprint matches.

Every parse, including an empty result, has a durable checkpoint. Segment replay keys include
source identity, text and structured control context. An unchanged section in a changed document
reuses its validated extraction but receives a new historical occurrence in the new revision.
Changing parsing/model configuration triggers intentional processing, not a fictional source revision.

The PostgreSQL adapter owns ORM tables. Units of work commit explicitly; otherwise they roll back.
Source/revision persistence, parse results and individual extraction checkpoints use separate short
transactions. A segment's concepts, relationships, exact evidence, usage and checkpoint commit
atomically. Leases fence obsolete workers and expire after five minutes; heartbeats renew them.

Source deletion/inaccessibility changes inventory status without deleting evidence. A failed
directory discovery does not mark its entire previous inventory missing. Restarting a scan resumes
failed/cancelled/expired tasks and reuses committed segments. A hard crash during a model call can
repeat that uncommitted call. There is no exactly-once billing guarantee.

### Relational design and durability

SQLAlchemy models declare indexed identifiers, foreign keys, uniqueness/check constraints and
JSONB payloads containing the full domain representation. Relationships are relational edges;
neither a graph database nor a vector index is required.

| Tables | Responsibility and principal links |
|---|---|
| `sources`, `source_revisions` | Current inventory and immutable content snapshots; revisions reference a source and optional predecessor |
| `segments`, `parse_checkpoints` | Ordered/hierarchical evidence and durable revision/parser markers, including empty parses |
| `publication_assessments` | Immutable revision/detector assessments with exact maturity/version evidence |
| `concepts`, `concept_versions` | Exact normalized identities and versioned interpretations with optional predecessors |
| `occurrences` | Exact excerpts/offsets connecting concept versions to persisted segments |
| `relationships` | Directed concept edges supported by an occurrence, with scope/status in the payload |
| `authority_events` | Append-only review/approval/rejection and promotion/withdrawal records, scoped and time-bounded |
| `processing_runs`, `processing_tasks` | Scan summaries and revision/fingerprint tasks with status, owner, lease, attempts and usage |
| `segment_results` | Validated extraction replay indexed by inference/context fingerprint and linked to its originating segment |
| `benchmark_documents` | Immutable normalized publisher records, exact passages and semantic occurrence bindings |
| `benchmark_comparisons` | Source-bound review-only comparisons, published MAPS_TO records and candidate semantic lineage |
| `benchmark_embeddings`, `benchmark_match_cache` | Provider/input-keyed vector and valid grounded-judgment replay |
| `pipeline_executions`, `pipeline_leases` | Pipeline/stage/check attempts, artifacts and renewable ownership |
| `enrichment_findings`, `reconciliation_decisions`, `concept_aliases` | Evidence-bound proposals and append-only reversible identity decisions |
| `knowledge_issues`, `issue_events` | Deduplicated issues, observation history and human resolution audit |

Artifact publication and database commits are not a distributed transaction. An interruption
between publishing an original and committing its revision can leave an unreferenced artifact.
Idempotent content-addressed writes make retry safe; no automatic artifact garbage collector
exists. Database backups and original-document storage must be retained together.

## Knowledge and authority

Objectives and requirements retain original evidence separately from interpreted statements,
textual modality, scope and confidence. Candidate evidence must match the persisted segment exactly,
including substring offsets. Relationship proposals carry their own evidence occurrence and scope.
Candidate status is independent of explicitly recorded promotion or withdrawal.

Exact identity uses normalized statement, kind, modality and scope. No embedding-based merging.
Inspection uses bounded lexical similarity; optional enrichment also uses cached embedding neighbors.
Neither path promotes or merges matches.
Stable concept identities, immutable versions, source occurrences and authority events preserve
history. Explicit version additions require a caller-supplied semantic version and predecessor;
automatic semantic version classification and promotion are intentionally absent.

Version predecessor references preserve explicit concept history. Published framework supersession
can use the separate `SUPERSEDES` relationship kind; published mappings use `MAPS_TO`.
Recursive origin/dependent queries traverse `DERIVES_FROM` and `DEPENDS_ON`;
SQL tracks visited paths and bounds depth to terminate cycles. Other relationship kinds are
available for inspection but are not treated as dependency lineage.

Initial concept semantic versions are unknown rather than invented. Explicit SemVer 2.0 versions
support prerelease/build identifiers and precedence, including concurrent draft branches.
Publisher edition labels and normalized source versions are separate from concept semantic versions.
Source-independent publication detection cites exact persisted text or metadata; immutable
assessments retain detector/parser history. Document metadata is not sent for semantic extraction.
Scoped authority projections distinguish review/approval from canonical promotion, apply effective
periods, and never infer authority from a source's final/approved status. See
`docs\decisions\0003-publication-and-semver.md` for migration and evidence decisions.
Reconciliation now provides minimal CLI, TUI and authenticated HTTP review. Accepted aliases,
canonical adoption and authority events commit atomically under an advisory lock with optimistic
decision heads. Reversal appends history rather than deleting occurrences.

`KnowledgeResolver` is independent of stage execution. Its canonical default requires accepted
reconciliation, applicable approved authority and source evidence; no candidate fallback exists.
`best_available` explicitly labels candidates, `historical` uses an explicit valid-time date, and
`all` retains drafts and superseded history without implying authority. Accepted aliases group
source occurrences; accepted source mappings add traceable native provenance. There is no
transaction-time reconstruction, exception-approval backend or enterprise role system.

## Interfaces and observability

Concise assertions are derived presentation candidates, not replacements for extracted statements.
`AssertionService` uses the replaceable `AssertionWriter` against an exact occurrence and its retained
section. A nullable JSONB column on `concept_versions` caches text, uncertainty, source/occurrence
bindings, model/prompt identity, input fingerprint and usage. Original version payloads, occurrence
offsets, semantic identity and authority remain unchanged. Unsupported rephrasing stays unresolved.
Workers and queues are bounded; unchanged inputs reuse cached phrasing. Browsing never invokes a model.
Knowledge search combines text and exact-source constraints on the same version; responses identify
that version and its source documents. Details retain exact quotes, their surrounding section and
adjacent passages from the same revision/parser; section labels distinguish neighboring material.

Logical sources are read projections over explicit benchmark-edition metadata, not destructive merges.
Each publisher/connector/edition has a stable group ID; v1 and v2 remain distinct. Ordinary documents
retain their existing source IDs. Member pages, workbooks, mappings and policies keep every original
source, revision, segment and occurrence. Group drill-down counts distinct evidenced versions and
aggregates passages across its members; knowledge filters constrain that same member-evidenced version
before paging. Assertion caches are reused only when their physical source belongs to the selection.
`/api/source-groups` serves the logical registry; `/api/sources` retains physical retrieval records.

Knowledge levels are derived read projections, not new semantic identities or authority states.
Objectives remain top-level; requirements with explicit platform scope are implementations.
Other requirements stay in goals/guidance; absent scope is not evidence of universal applicability.
Source, text and level filters constrain the same version before paging. Original payloads stay intact.
Graphs place goals/guidance above implementations and keep source evidence separate. Benchmark links
resolve current, exact framework/revision/control identifiers into native control nodes, not arbitrary
clauses. Published `MAPS_TO` carries its original mapping quote; subcontrols stay implementation details.
These links establish neither equivalence, clause coverage, compliance nor company-goal alignment.
Missing or ambiguous mappings are not drawn; review proposals are opt-in and dashed.

All interfaces share `KnowledgeService`. No web server is required for CLI or TUI operations.
The web scan task is asynchronous and cancelable on shutdown; durable database checkpoints survive
process loss. The separate worker periodically scans configured connectors using the same pipeline.
Multiple workers may rediscover sources, but atomic claims prevent duplicate committed processing.

Application OpenTelemetry spans cover scans, discovery, comparison, download, parsing, selective
OCR, segmentation, inference, validation, transactions, concept/relationship persistence and retries.
Exception type/status is recorded, never exception messages containing document text or SQL
parameters. Logs carry trace/span correlation. JSONL files rotate by configured size and retention
inside `data\telemetry\<process-id>`; separate CLI/UI writers cannot collide during Windows rotation.
Retention limits apply per process; old process directories require operational housekeeping.
Azure adds Application Insights trace, log and metric exporters to the same instrumentation.
Pipeline, check, reconciliation, resolution and issue spans retain run/finding correlation;
rotating `metrics.jsonl` captures run/check/finding/issue/resolution counters.

Usage records cover committed validated calls and persist across resume. Token counts are provider
reported; unavailable counts stay zero, not estimated. Failed/uncommitted calls can incur provider
charges not included in committed task totals; call spans retain returned token counts. Cost
estimates require configured per-million-token prices and are not invoices.

Debug payload capture is opt-in and separate from telemetry. Its files include exact model
messages, output, model/prompt identity and trace references, but never authorization headers.
Restrict the debug/data directory with operating-system ACLs, especially on Windows.

### Interface responsibilities

| Interface | Behavior |
|---|---|
| Typer CLI | Migration, doctor, scan/resume/dry-run, inventory, concept/provenance/lineage inspection, evaluation and process launch |
| Textual terminal UI (TUI) | Responsive inventory/progress, concepts, provenance, relationships and usage; invokes the same pipeline |
| FastAPI + React/shadcn | Local compiled browser assets and JSON inspection; scan submission starts an application-owned async task |
| Model Context Protocol (MCP) | Twelve read-only tools; authoritative consumers default to `resolve_knowledge` / `resolve_requirement` |
| Periodic worker | Runs the same pipeline with configured connectors; not a separate implementation or broker consumer |

The web scan form accepts configured sources, not arbitrary browser-supplied filesystem paths.
Its same-origin checks and single in-process scan slot are not enterprise authentication or
distributed scheduling. The task is cancelled on shutdown; durable checkpoints support later runs.
Governance mutations are separately disabled until `EKB_GOVERNANCE_TOKEN` is configured. HTTP
Basic identifies a local operator and requires HTTPS or loopback plus same-origin mutation checks.
Read endpoints remain local/private; this is not enterprise authentication or tenant authorization.

The browser has a task-oriented hierarchy: overview, searchable knowledge, sources, review queue,
benchmark explorer and pipeline activity. Exact evidence, history, scope and technical identifiers
are progressively disclosed. Actual shadcn components use Clawpilot tokens, keyboard-accessible
controls and light/dark responsive layouts. TypeScript DTOs describe API responses; no browser code
infers applicability, runs models or grants authority.

Vite builds local assets into the Python package. Committed output permits Python-only checkout,
wheel and container operation; source fingerprints reject stale builds. Knowledge and proposal
lists use exclusive UUID cursors with bounded queries, not fake pagination over a truncated corpus.
The former HTMX partials are retired; existing browser addresses and server review POSTs remain.
See `decisions\0006-browser-presentation.md` and `frontend\README.md`.

## Deployment and configuration

Local development runs Python on the host with Compose PostgreSQL 17 and a filesystem artifact
root. The optional Compose `app` profile runs web and periodic worker containers from one image;
they share a named data/artifact volume and database. PostgreSQL and published web ports bind
to localhost. The non-root image contains Python 3.13 and English Tesseract for opt-in OCR.

The Azure foundation selects the same application with PostgreSQL, private Blob Storage,
configured Foundry/OpenAI-compatible inference, managed identity where supported, and Application
Insights exporters. `config\azure.yaml` and `deploy\azure\container-apps.env.example` describe
settings, not provisioned resources. Infrastructure deployment, identity assignments, network
isolation and enterprise endpoint authentication remain outside this implementation.

`Settings` precedence, highest first: explicit constructor values, `EKB_` environment variables,
`.env`, YAML selected by `EKB_CONFIG_FILE`, file-secret settings, defaults. Telemetry configures
once per process; changing settings is not a hot-reload mechanism.

## Design decisions and extension seams

- Short explicit transactions, append-only evidence and idempotent checkpoints are recorded in
  `docs\decisions\0001-provenance-and-transactions.md`.
- The async modular monolith, deterministic parser fallback and non-autonomous inference boundary
  are recorded in `docs\decisions\0002-runtime-and-dependencies.md`. Docling is not installed;
  PyMuPDF and Office-specific parsers are the implemented path.
- Provider changes belong behind existing `Protocol` contracts and the explicit composition root.
  Connector factories are discoverable through `ekb.connectors`; shipped runtime configuration
  constructs built-ins directly. Registration is not automatic activation.
- Replacement repositories must retain unit-of-work, lease, history and checkpoint semantics.
  Parsers must retain locations and update fingerprints when output changes. Inference providers
  must expose capabilities and pass schema/grounding checks.

## Known MVP limits

- The offline rules baseline is deliberately limited: it misses implicit objectives, cannot infer
  scope and may classify CSF outcomes as requirement candidates. It is not production model quality.
- Model and Azure interfaces have automated offline contracts; no paid model benchmark, Azure
  deployment or live managed-identity verification is claimed.
- PDF headings and reading order are heuristic; Office page layout, complex tables, speaker note
  formatting and diagram semantics are not reconstructed perfectly. Images retain original references;
  image-only segments are not sent to a text model.
- OCR is selective and opt-in. Tesseract/language data are needed on the host; the container includes
  English Tesseract. Cancellation can wait for an already-running blocking parser thread to finish.
- Embedding retrieval and small-model verification reuse benchmark caches for optional generic
  enrichment as well as benchmark comparison. Candidate budgets are bounded, not
  exhaustive cross-corpus discovery. No autonomous compliance decisions, semantic merges
  or exception-management workflow.
- Source manifests can change upstream; original bytes/hashes are retained, but upstream URLs are not
  guaranteed immutable. ISO entries are metadata only; licensed text must be supplied locally.
- Inspection results are bounded (200 concepts per query, up to 1,000 tasks for statistics). This MVP
  has no pagination UI or enterprise authorization. Keep interfaces local/private.
- Actual benchmark synchronization, structured ingestion, mappings/policy retrieval, native NIST
  resolution and review-only comparisons are implemented. Structural proposals are not verified
  semantic decisions; source gaps and unresolved references remain explicit.
- Reconciliation snapshots explicitly fail beyond 20,000 rows per workspace table; partitioning is
  required at that point. Parsed interpretations of unchanged bytes remain in candidate history;
  strict explicit canonical decisions, not parser recency, establish authority.

## Evidence

- `src\ekb\runtime.py`, `src\ekb\config\__init__.py`: composition, resource ownership and settings.
- `src\ekb\ports\__init__.py`, `src\ekb\pipeline\__init__.py`: contracts and ingestion flow.
- `src\ekb\domain\models.py`, `src\ekb\domain\grounding.py`: semantic values, identity and evidence.
- `src\ekb\adapters\persistence\postgres\models.py`,
  `src\ekb\adapters\persistence\postgres\__init__.py`: schema, repositories and leases.
- `src\ekb\adapters\sources\__init__.py`, `src\ekb\adapters\parsers\__init__.py`,
  `src\ekb\adapters\inference\__init__.py`, `src\ekb\adapters\artifacts\__init__.py`:
  concrete processing and provider behavior.
- `src\ekb\services\knowledge.py`, `src\ekb\interfaces`, `src\ekb\observability\__init__.py`:
  inspection, transport lifetimes and instrumentation.
- `Dockerfile`, `compose.yaml`, `config\azure.yaml`, `deploy\azure\container-apps.env.example`:
  container and deployment contracts.
- `tests\contract\test_repositories.py`, `tests\integration\test_pipeline.py`,
  `tests\integration\test_interfaces.py`: verification paths for principal guarantees.
