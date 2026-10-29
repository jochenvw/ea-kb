# Architecture and design

EKB is an asynchronous modular monolith. PostgreSQL is the authoritative store;
original documents live in content-addressed filesystem or Azure Blob artifacts.

## Scope and design drivers

This describes the implementation through commit `9ea2a35`, not every capability requested in
`goal.md` or `goal2.md`. The system extracts objective and requirement candidates while retaining
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
| `adapters` | Connectors, parsing, inference, artifacts, PostgreSQL | Transport interfaces |
| `interfaces` | CLI, Textual, FastAPI/Jinja2/HTMX, read-only MCP | Direct ORM queries (except database health checks) |
| `runtime.py` | Explicit dependency injection and resource lifetimes | Nothing implicit or global for business state |

`SourceConnector -> change detection -> ArtifactStore -> DocumentParser -> Segment ->
InferencePort -> evidence validation -> KnowledgeRepository`

Independent discovery producers feed a bounded queue. Worker count limits source downloads;
separate semaphores bound blocking parsers/OCR and inference. Blocking Office/PDF libraries run
in threads. HTTPX and SQLAlchemy sessions perform asynchronous I/O.

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

Occurrence offsets delimit text in the persisted parsed segment, not byte offsets into PDF or
Office originals. Segment locations and the revision's artifact key bridge normalized text back
to the preserved document; each revision snapshots its historical source location and metadata.

The standalone `scripts\download_test_corpus.py` is a download utility, not another ingestion
service. Its `config\test_corpus.yaml` and retrieval index do not replace the pipeline's
`sources\public.yaml` manifest or PostgreSQL checkpoints.

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
Related-candidate retrieval uses bounded lexical similarity without promoting or merging matches.
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
There is still no end-user approval/version-management workflow.

## Interfaces and observability

All interfaces share `KnowledgeService`. No web server is required for CLI or TUI operations.
The web scan task is asynchronous and cancelable on shutdown; durable database checkpoints survive
process loss. The separate worker periodically scans configured connectors using the same pipeline.
Multiple workers may rediscover sources, but atomic claims prevent duplicate committed processing.

Application OpenTelemetry spans cover scans, discovery, comparison, download, parsing, selective
OCR, segmentation, inference, validation, transactions, concept/relationship persistence and retries.
Exception type/status is recorded, never exception messages containing document text or SQL
parameters. Logs carry trace/span correlation. JSONL files rotate by configured size and retention.
Azure adds Application Insights trace and log exporters to the same instrumentation.

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
| FastAPI/Jinja2/HTMX | JSON inspection and server-rendered pages; scan submission starts an application-owned async task |
| Model Context Protocol (MCP) | Stdio, read-only `search_concepts`, `get_concept`, `trace_provenance` and `find_related_requirements` |
| Periodic worker | Runs the same pipeline with configured connectors; not a separate implementation or broker consumer |

The web scan form accepts configured sources, not arbitrary browser-supplied filesystem paths.
Its same-origin checks and single in-process scan slot are not enterprise authentication or
distributed scheduling. The task is cancelled on shutdown; durable checkpoints support later runs.

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
- No embeddings, exhaustive graph extraction, automatic cross-corpus relationship discovery,
  autonomous compliance decisions, semantic merges or exception-management workflow.
- Source manifests can change upstream; original bytes/hashes are retained, but upstream URLs are not
  guaranteed immutable. ISO entries are metadata only; licensed text must be supplied locally.
- Inspection results are bounded (200 concepts per query, up to 1,000 tasks for statistics). This MVP
  has no pagination UI or enterprise authorization. Keep interfaces local/private.
- Benchmark-edition comparison, Microsoft Cloud Security Benchmark mappings/Azure Policy retrieval
  and semantic reconciliation in `goal2.md` are not yet implemented.

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
