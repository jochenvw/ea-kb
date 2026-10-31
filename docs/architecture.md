# Architecture and design

EKB is an asynchronous modular monolith. PostgreSQL is the authoritative store;
original documents live in content-addressed filesystem or Azure Blob artifacts.
It is a general enterprise architecture knowledge tool: sample publishers and industries belong
in source manifests and scope metadata, not organization-specific domain logic.

The application is `0.1.0`, registered in `pyproject.toml` and read through installed package metadata.
Before major version 1, data/schema compatibility is not required: `ekb init --recreate` rebuilds
EKB-owned tables without re-indexing. It preserves originals, unrelated tables and source inputs,
refuses active work and is disabled from v1 onward. Ordinary startup is not destructive.
Development initialization uses current ORM metadata, not migration history; see decision `0009`.

## Scope and design drivers

This describes implemented ingestion and governed reconciliation, not every planned
capability. The system extracts strategic-goal and ontology requirement candidates while retaining
the evidence needed to inspect each interpretation. Documents, interpretations and enterprise
approval are separate concerns.

The design favors recoverable incremental processing, bounded resource use and explicit
infrastructure boundaries over exhaustive graph construction or autonomous agent execution.
`docs\codebase` maps the canonical guides, manifests and test entry points without duplicating them.
Coding rules live in `AGENTS.md`; extension contracts live in
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

`SourceConnector -> deterministic inventory/acquisition barrier -> ArtifactStore ->
DocumentParser -> Segment -> labeling skill -> scoped extraction skill ->
evidence/scope validation -> KnowledgeRepository`

Independent discovery producers feed a bounded queue. Worker count limits source downloads;
separate semaphores bound blocking parsers/OCR and inference. Blocking Office/PDF libraries run
in threads. HTTPX and SQLAlchemy sessions perform asynchronous I/O.

Publisher documents follow the same connector, parser and ontology-skill path as every other source.
Names, titles, versions, licenses and explicitly evidenced platforms remain source facts; an overview
does not imply that linked documents were acquired. Requirements are not classified from a publisher
or identifier alone, and unsupported relationships are not invented.

`PipelineOrchestrator` composes `indexing -> enrichment -> issues`; indexing invokes this same
ingestion engine. Versioned stage/check contracts and input/configuration fingerprints control
incrementality. A renewable 120-second lease fences concurrent runs; heartbeat failure cancels work.
Renewal and finalization require the same unexpired owner and cannot recreate a removed lease.
Successful upstream artifacts are checked for matching contracts, source revision and producer
configuration before partial runs. Independent checks persist their own execution/cache records.
See `phase3.md` for plugin registration and the actual-corpus vertical slice.

### Bounded skills and precision-gated frugality

Microsoft Agent Framework is the preferred model execution adapter, not the domain or a second
orchestrator. Model-backed profiles select `agent-framework`; `openai` remains an explicit
replaceable transport and `rules` remains the limited, no-paid-call default. No autonomous agent,
tool loop, dynamic planner or extra inference pass is needed for deterministic work.

Separate distinct semantic responsibilities into skills with typed inputs/outputs, editable prompt
assets, independent fingerprints and evaluations. The implemented ingestion skills are:

| Phase | Mechanism | Durable output |
|---|---|---|
| Inventory/acquire | Deterministic connector discovery, revision checks and bounded retrieval | Source inventory and original revisions; unsupported local formats are metadata-only |
| Parse | Deterministic parsers/OCR and parse replay | Exact source segments and locations |
| Label | `IngestionSkills.label`, `skills\labeling.md` | Evidence-backed, multi-label ontology types and explicit scopes |
| Extract within labels | `IngestionSkills.extract_scoped`, `skills\extraction.md` | Typed candidates/relationships restricted to the labeled evidence, type and scope |
| Reconcile strategy | `StrategyReconciliationPort.reconcile_strategy`, `skills\strategy_reconciliation.md` | Compact, deduplicated source-level goals and fundamental requirements with safe lower-layer reclassification |

Inventory/acquisition completes before semantic processing starts, including ordinary `scan`.
The historical `indexing` orchestration stage still wraps the complete ingestion use case; its name
does not mean all of its internal work is model-free. Parsing and rules remain deterministic; optional
model-backed skills run only when explicitly configured.

Architecture layer and applicability are independent: a business-unit goal is still a
`StrategicGoal`, not a technical requirement. Typed labels use the seven OWL class identifiers in
`ontology\ontology\ekb.ttl`; `OrganizationalScope` describes applicability. Unknown layers remain
null with an explanation. An explicitly named, source-grounded, platform-independent area of
architectural concern may be materialized as an `ArchitectureDomain` concept; it remains a
classification concept, not a requirement. The OWL catalog loader performs no reasoning or scope
inheritance. An explicit parsed section heading may ground a named organizational-unit scope for
content in that section; generic headings, filenames and publishers cannot.

Label checkpoints commit separately before extraction, reusing the existing typed JSON replay
store without a schema migration. Extraction-only changes/failures reuse labels; a successful
segment still commits candidates, evidence, extraction replay and newly incurred extraction usage
atomically. Label results are retained in extraction replay for audit. Existing historical concept
versions/identities are not rewritten when a new label becomes available. During pre-v1 development,
breaking ontology/schema changes may rebuild disposable state instead of historical relabeling.

Use the smallest/cheapest model **that passes the task's precision and safety gates**, not simply
the smallest deployment. `EKB_LABELING_MODEL_ID` can select a separate cheaper labeler on the same
endpoint; blank reuses the extraction deployment. Separate labeler prices prevent unknown cost
from becoming zero. Failed validation stays a failure after bounded retries; there is no silent
model downgrade. Grounding and scope checks are necessary, not proof of semantic correctness.
Model/prompt changes require independently evaluated skill results and end-to-end checks on
document-family-isolated validation/test data. See `evaluation.md` and decision `0007`.

## Components and runtime composition

### Ontology as ubiquitous language

`ontology\ontology\ekb.ttl` is authoritative for class/property identifiers and English definitions.
`adapters\ontology.py` uses RDFLib to read the same source in a checkout, wheel and container.
The read-only `/api/ontology` catalog and `/ontology` browser expose concepts, semantic relations,
classification properties, exact definitions, IRIs, version, domain/range and OWL characteristics.
No database, inference call, ontology editing or OWL reasoning is required to view the vocabulary.

`SemanticType` covers all seven classes; extracted concepts use `semantic_type`, not a second
objective/requirement kind. Organizational scopes remain applicability metadata rather than
candidate concepts. Architecture domains are retained only when the source explicitly names the
technology-independent concern; they are never inferred from a publisher, document title or heading.
Unsupported classifications stay null, not an invented class.
`concept_versions.semantic_type` is indexed, SQL-constrained to ontology classes and checked against
its candidate payload. Classification belongs to each interpretation/version, not a timeless record.
Initial identity includes semantic type; explicit later interpretations retain their predecessor chain.
`RelationshipKind` and the SQL relationship constraint allow only the seven semantic property names.
Scope facets retain source labels: organization/business unit describe `appliesToScope`, and platform
describes `platformName`; they do not establish normalized scope identities or inferred containment.
The catalog also exposes `inDomain` and `subScopeOf`; no hierarchy is inferred from free-text facets.
Version succession and comparison overlap are lifecycle/comparison metadata, not semantic relations.
Prompts include the canonical definitions; UI concept badges link back to the catalog.
`tests\unit\test_ontology.py` checks OWL/domain/SQL/browser parity, and integration tests exercise
real SQL constraints and schema recreation. See decision `0008`.

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
`ekb init` creates the current ORM schema during pre-v1 development; `--recreate` explicitly rebuilds
it after breaking changes. From v1, initialization uses Alembic. Web startup never changes the schema.

Source absence uses the shared `SourceMissingError` port contract. HTTP adapters own transient
retry classification and backoff; the pipeline handles provider-independent outcomes.
An AST regression test keeps infrastructure imports out of domain, ports and application layers.

### Evaluations and frugal provider selection

`evaluation\dataset.py` derives versioned extraction, comparison and retrieval cases from attributed
teacher annotations, with document-family train/validation/test isolation. `distillation.py` exports
only training labels; `frugal.py` runs actual providers without sending expected answers and applies
grounding, quality, latency, token and priced-cost gates. `suite.py` combines the existing code tests
and evaluation results into one persisted report/exit status. Offline safety success is explicitly
not live model qualification; teacher labels are provisional. See `evaluation.md`.
`evaluation\skills.py` executes isolated cases with Pydantic Evals and retains EKB's domain scorers.
Typed, create-only history indexes support the skill catalog without loading case-level reports;
corrupt indexes fail explicitly. See decision `0010`.

Generic semantic enrichment unions lexical candidates with independently retrieved cosine neighbors
from a bounded assertion pool. Completed checks are durably replayed. A single generic cache also
reuses validated embeddings and grounded verification results across check versions when the provider
and exact semantic inputs match; changed inputs or provider fingerprints can incur new calls.
`infra\main.bicep` describes minimal Entra-only Foundry model resources, with an optional teacher
disabled by default. Configuration/deployment does not establish provider quality.

## Ingestion and inspection flows

1. `ekb scan` constructs `Runtime` and connectors. Discovery yields stable identifiers, locations,
   formats, metadata and inexpensive revision indicators. Metadata-only sources become inventory
   records without downloads, source revisions or inference.
2. The pipeline reads the previous source/revision/task. Matching available-source metadata and a
   completed processing fingerprint skip work; otherwise retrieval uses local reads or conditional
   HTTP. Changed bytes are hashed and stored before a source revision is committed.
3. An atomic database claim selects one owner for a revision/fingerprint. A completed task is skipped;
   a still-leased task is skipped by standalone scans but makes an orchestrated run incomplete.
   Existing parse checkpoints are reused; otherwise the parser produces stable segments with page,
   heading, table, slide or structured-control locations.
4. Each usable segment reuses or executes its labeling skill, commits that checkpoint, then reuses
   or executes extraction within the labeled spans/scopes. Results must satisfy schema, exact
   grounding, ontology classification consistency and qualifier/scope checks. Domain-only and irrelevant
   content skips model extraction. Image-only segments remain references and do not enter text inference.
5. Model-backed ingestion defers all `StrategicGoal` and `StrategicRequirement` candidates until
   bounded source-level reconciliation. The reconciler removes duplicate goal formulations, keeps
   company obligations compact, preserves scope/metrics/dates and may reclassify overpromoted
   requirements only to an evidence-supported ontology class. A manifest source may set
   `extract_strategy: false` when it corroborates a designated strategy source; its non-strategy
   knowledge remains eligible, and the setting participates in the processing fingerprint.
6. Ownership checks, candidate/relationship persistence, extraction checkpoints and newly incurred
   usage commit together. Task/scan records expose completion, partial failure or cancellation.
7. Inspection bypasses ingestion: an interface calls `KnowledgeService`, which opens a unit of
   work and returns domain values. Provenance joins occurrences, segments, source revisions and
   source inventory; retrieving an explanation does not require a model call.

The browser puts shared language first, then **Consolidate**, **Reference** and **Operations**.
The consolidation path is source curation, interpretation review, visual validation and scoped
decisions; it is guidance, not a persisted completion state. Source selection remains operator-configured.
The `InspectionRepository` port
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
It downloads only the entries explicitly listed in its own manifest.

## Execution and concurrency

Defaults are four source workers, a queue of eight items, two parser slots and two inference slots.
Settings can change these limits. Segments within one source are processed in order; concurrency
comes from multiple source workers. There is no external message broker: queues are process-local,
while claims/checkpoints are durable in PostgreSQL.

Transient HTTP transport failures, status 408/429 and server errors use bounded backoff with
jitter inside adapters, not the pipeline. Schema/grounding retries have a separate model limit.
Permanent source failures remain
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
content, schemas, model capabilities, independent labeling/extraction prompts and configuration. Metadata filters avoid
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
Each non-dry-run scan independently holds a 120-second renewable guard from before its first report
through finalization. Its unit-of-work commits verify ownership under a database row lock. Reset
serializes with those commits and can disregard an expired guarded scan, but not an unguarded legacy
`running` report whose liveness is unknown. See `docs\plugins.md` for replacement-store semantics.

Source deletion/inaccessibility changes inventory status without deleting evidence. A failed
directory discovery does not mark its entire previous inventory missing. Restarting a scan resumes
failed/cancelled/expired tasks and reuses committed segments. A hard crash during a model call can
repeat that uncommitted call. There is no exactly-once billing guarantee.

Pre-v1 reset/recreation removes four obsolete benchmark tables and legacy `concept_aliases` in the
active schema in the same guarded transaction as current table cleanup. The current schema derives
alias edges instead. Cleanup does not cascade or delete source inputs or filesystem/Azure originals;
dependencies from unrelated tables fail and roll back.

### Relational design and durability

SQLAlchemy models declare indexed identifiers, foreign keys, uniqueness/check constraints and
JSONB payloads containing the full domain representation. Relationships are relational edges;
neither a graph database nor a vector index is required.

The original 24 application tables, excluding Alembic bookkeeping, become 20: remove four
specialized benchmark tables and `concept_aliases`, then add `semantic_replay_cache`
(`24 - 4 - 1 + 1 = 20`). The retained tables are:

| Table | Responsibility and principal links |
|---|---|
| `sources` | Stable source identities, locations and current inventory metadata |
| `source_revisions` | Immutable content snapshots linked to a source and optional predecessor |
| `publication_assessments` | Immutable revision/detector assessments with exact maturity/version evidence |
| `segments` | Ordered, hierarchical parsed evidence bound to a revision and parser fingerprint |
| `parse_checkpoints` | Completed revision/parser combinations, including empty parses |
| `concepts` | Exact normalized concept identities |
| `concept_versions` | Versioned interpretations, optional predecessors and derived assertion cache |
| `occurrences` | Exact excerpts/offsets connecting concept versions to persisted segments |
| `relationships` | Directed concept edges supported by an occurrence, with scope/status in the payload |
| `authority_events` | Append-only review/approval/rejection and promotion/withdrawal records, scoped and time-bounded |
| `processing_runs` | Standalone or nested scan summaries and outcomes |
| `processing_tasks` | Revision/fingerprint tasks with status, owner, lease, attempts and usage |
| `segment_results` | Validated skill replay indexed by fingerprint and linked to its originating segment |
| `pipeline_executions` | Pipeline/stage/check attempts, results and artifacts |
| `pipeline_leases` | Renewable pipeline ownership and fencing |
| `semantic_replay_cache` | Provider/input-keyed validated embeddings and grounded verification judgments, independent of check versions |
| `enrichment_findings` | Evidence-bound proposals produced by enrichment checks |
| `reconciliation_decisions` | Append-only, reversible finding decisions and decision heads |
| `knowledge_issues` | Deduplicated issues, observations and lifecycle state |
| `issue_events` | Issue review and resolution audit history |

Alias edges are derived from immutable findings and decision heads, including historical effective
dates and reversals; no second alias projection is written. Snapshots and decision histories are
bounded to 20,000 records. Acceptance uses a partial alias-finding index and indexed decision-head
lookup, and the governance transaction lock serializes conflicting reviews. See decision `0011`.

The small `processing_runs` table remains separate from `pipeline_executions`: standalone and nested
scans have `ScanReport` counts and completed/partial statuses, while pipeline executions have
parented stage/check artifacts, ownership and different status semantics. Mixing scan roots into
execution queries would require a second payload variant and special filters for current pipeline
and enrichment outputs. `parse_checkpoints` separately proves completion even for zero segments;
`segment_results` retains independently fingerprinted skill outputs across revisions.

Task status and attempt columns are authoritative for claims and are overlaid on the domain
payload when read; usage remains in that payload. Pipeline execution projections and payloads
are written together by `Workflow.save_execution`; ontology type projections are SQL-checked
against their immutable version payloads. Connector identity and finding input revision remain
in their typed payloads: no retained query or constraint needs duplicate SQL columns for them.

Artifact publication and database commits are not a distributed transaction. An interruption
between publishing an original and committing its revision can leave an unreferenced artifact.
Idempotent content-addressed writes make retry safe; no automatic artifact garbage collector
exists. Database backups and original-document storage must be retained together.

## Knowledge and authority

Strategic goals and requirements retain original evidence separately from interpreted statements,
textual modality, scope and confidence. Candidate evidence must match the persisted segment exactly,
including substring offsets. Relationship proposals carry their own evidence occurrence and scope.
Candidate status is independent of explicitly recorded promotion or withdrawal.

Exact identity uses normalized statement, ontology semantic type, modality and scope. No embedding-based merging.
Inspection uses bounded lexical similarity; optional enrichment also uses embedding neighbors.
Neither path promotes or merges matches.
Stable concept identities, immutable versions, source occurrences and authority events preserve
history. Explicit version additions require a caller-supplied semantic version and predecessor;
automatic semantic version classification and promotion are intentionally absent.

Version predecessor references and reviewed version findings preserve explicit concept history.
Published framework succession remains lifecycle metadata; an evidenced semantic relation may use
`mapsTo` without assuming equivalence or resolving an unsupported target.
Recursive origin/dependent queries traverse `refines` and `dependsOn`;
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
source occurrences; accepted relations retain traceable provenance. There is no
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

`/api/sources` exposes the individual source inventory. Source drill-down and search select only
versions evidenced by that source, and assertion caches are reused only for their evidenced source.

Knowledge levels are derived read projections, not new semantic identities or authority states.
The **company** layer contains `StrategicGoal` and `StrategicRequirement`; its focused workspace
shows enterprise goals and strategic requirements, while subordinate and business-unit goals remain
available in the diagnostic all-knowledge view. The
**platform-agnostic** layer contains `ArchitectureDomain`, `ConceptualRequirement` and
`TechnicalRequirement`; the **platform-specific** layer contains `PlatformRequirement`.
Unclassified interpretations remain separate. These layers drive the `/company`, `/architecture`
and `/platforms` workspaces and graph lanes; they do not assert ontology subclassing, authority or
universal applicability. Source, text, semantic-type and level filters constrain the same version
before paging. Original payloads stay intact. Graphs keep source evidence separate and show only
persisted, evidenced ontology relationships and review proposals; missing or ambiguous links are
not drawn. Relationships do not establish equivalence, compliance or company-goal alignment.

All interfaces share `KnowledgeService`. No web server is required for CLI or TUI operations.
Ingestion runs through CLI commands, including the independent `ekb worker`; web startup and
shutdown do not own or cancel it. Durable database checkpoints survive process loss. The worker
periodically scans configured connectors. Multiple workers may rediscover sources, but atomic
claims prevent duplicate committed processing.

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
| Textual terminal UI (TUI) | Source and concept inventory, provenance, relationships and governed review |
| FastAPI + React/shadcn | Local compiled browser assets, knowledge inspection and authenticated governance |
| Model Context Protocol (MCP) | Eight read-only tools; authoritative consumers default to `resolve_knowledge` / `resolve_requirement` |
| CLI-launched periodic worker | Runs the same ingestion engine with configured connectors; not a separate implementation or broker consumer |

Web/API and TUI neither execute ingestion nor provide dedicated processing monitors; the CLI follows
foreground progress and reads persisted status. Governance mutations are disabled until
`EKB_GOVERNANCE_TOKEN` is configured. HTTP
Basic identifies a local operator and requires HTTPS or loopback plus same-origin mutation checks.
Non-health web/API routes reject non-loopback clients and forwarded-proxy headers. Remote access
and enterprise identity/tenant authorization are not implemented.

The browser starts with a standards-consolidation workspace and the canonical shared language.
The supported browser target is desktop/PC use only; mobile-specific UX and verification are out of scope.
Sources, interpretations, visual validation and reconciliation form the architect's work sequence;
decision history is reference material and generic data/log inspection remains available.
Interpretations show the canonical classification definition and exact evidence before visual validation.
Evidence and scoped authority belong to the selected version; adoption proposals default to that version.
Graphs require an explicit starting point and remain bounded concept neighborhoods across versions,
not global coverage or approval views. Source curation checks do not claim a saved approval state.
History, scope and technical identifiers are progressively disclosed. Actual shadcn components use
Clawpilot tokens, keyboard-accessible
controls and light/dark responsive layouts. TypeScript DTOs describe API responses; no browser code
infers applicability, runs models or grants authority.

Vite builds local assets into the Python package. Committed output permits Python-only checkout,
wheel and container operation; source fingerprints reject stale builds. Knowledge and proposal
lists use exclusive UUID cursors with bounded queries, not fake pagination over a truncated corpus.
The former Jinja/HTMX presentation is retired; browser routes serve the prebuilt HTML file directly.
Retained knowledge addresses and server review POSTs remain.
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
- Optional embedding retrieval and small-model verification operate on generic evidenced assertions.
  Completed check replay avoids repeated work, but changed checks can incur new calls. Candidate budgets
  are bounded, not
  exhaustive cross-corpus discovery. No autonomous compliance decisions, semantic merges
  or exception-management workflow.
- Source manifests can change upstream; original bytes/hashes are retained, but upstream URLs are not
  guaranteed immutable. ISO entries are metadata only; licensed text must be supplied locally.
- Inspection queries are bounded (200 concepts per query, up to 1,000 tasks for statistics).
  Concept, source-extraction and proposal pages use cursors; statistics are not whole-corpus analytics.
  Enterprise authorization is absent. Non-loopback requests are denied; keep local.
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
