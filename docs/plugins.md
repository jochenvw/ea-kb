# Adapter and plugin contracts

## Connectors

Package entry points in group `ekb.connectors` map provider names to connector factories.
The shipped `filesystem`, `http`, `oscal` and `microsoft-security-benchmark` entries are tested.
`load_plugin(name, configuration)` constructs the selected factory through explicit injection.
The composition root uses built-in connectors directly; adding an entry point alone does not
automatically grant network access or enable a source.

The benchmark factory takes an `ArtifactStore`, a bounded concurrency and optional injected HTTP
client. `sync()` is the explicit network boundary; normal discovery/fetch only reads validated cache.
It prefers official Learn Markdown, accepts validated HTML, discovers actual workbook paths from the
linked public repository index and resolves published policy IDs to official JSON links. Focused sync
does not prune other editions. Publication/mapping records do not become semantic types or approvals.

A connector implements `name`, `discover()` and `fetch(item, previous)`. Discovery yields a
stable ID, location, format, inexpensive revision indicator and metadata. Fetch receives the last
durable source checkpoint and returns bytes or an explicit not-modified result. Local checkpoints
use file metadata; HTTP checkpoints retain ETag/Last-Modified. Discovery completion is distinct
from inaccessible discovery: only a complete inventory can establish missing sources.
Confirmed absence raises `ekb.ports.SourceMissingError`; other retrieval failures remain inaccessible.
Connector adapters own bounded transient transport retries; the pipeline does not wrap them in a
second retry policy.

Future SharePoint support should add durable connector-level cursor storage and use Microsoft
Graph delta URLs and a credential abstraction. Do not equate an inaccessible page with deletion.
No SharePoint/Graph credentials, proprietary integrations, or fabricated remote documents ship here.

## Parsing and inference

`DocumentParser` returns stable segments with revision IDs, hierarchy, locations, quality and media
references, with no model calls. Parser configuration must change its fingerprint whenever output
may change. Preserve originals; normalized text is an intermediate artifact, not a substitute.
Native CSF PDF parsing retains source Function/Category/Subcategory IDs and exact per-page text in
segment locations; these are not EKB classes. Whole-publication detection uses actual cover text,
not filenames. Explicit `framework_namespace: nist-csf` requires the complete Core hierarchy.

`InferencePort` exposes capabilities, prompt version and validated structured extraction.
`IngestionSkills` adds `label(segment)` and `extract_scoped(segment, labeling)` with independent
label/extraction fingerprints. Labeling returns exact evidence spans, nullable ontology classes
and explicit scopes; extraction must retain each candidate's matching class/scope and evidence.
Optional `ContentLabeling.coverage` independently identifies `company_level`, `organizational_unit`
or explicit `unknown`, with an exact supporting excerpt. Coverage applies only to supplied content,
does not identify an adopting company, and is not an ontology class or authority decision.
Multiple ontology labels must survive company-level classification; external frameworks may span
strategic requirements, architecture domains and lower requirements without becoming approved policy.
Domain classification is not a requirement. Unknown layers require an uncertainty explanation.
Prompt assets live in `adapters\inference\skills` and their content hashes enter replay identity.
Implement both skills for an interchangeable ingestion provider; `extract` is the combined
model-backed entry point used by end-to-end evaluations. Rules remain a limited diagnostic baseline.

Use explicit model configuration; do not assume every endpoint supports native JSON schemas,
vision, embeddings or a particular context length. Adapter output is validated against Pydantic
and exact source evidence before persistence. Inference has bounded concurrency, transient retries,
bounded schema retries and replayable results. The supplied optional Agent Framework adapter uses
a bounded Chat Completions client, not an autonomous agent. It preserves configured temperature and
token-budget parameter names and disables SDK-internal retries in favor of the shared retry boundary.
Model-backed profiles prefer this adapter; the direct OpenAI-compatible transport remains available.
Live skill evaluations require a conservative `max_requests_per_skill` bound that includes all
validation and transport retries, or an explicitly supplied bound for a replacement provider.
To support optional evaluation rate limits, implement `RequestControlledSkills` and reserve via
the supplied `ModelRequestLimiter` immediately before every actual provider request, including
retries, using the actual deployment ID. The scoped hook must restore state on cancellation; use a
dedicated inference instance for the evaluation. Report provider model IDs in response order and
mark identity completeness only when every request's identity is known. Injected HTTP transports
must disable their own retries so these bounds cannot be bypassed.

Labels use the existing replay store with an empty extraction and typed `labeling` result; its
checkpoint commits before extraction, including newly incurred labeling usage under ownership fencing.
Extraction replay includes routing content and configuration and retains the label result.
Adapters must not turn a cache hit into another model call or bill cached usage again.

Extraction also proposes directly evidenced local relationships. Enrichment independently runs
bounded deterministic checks; optional `SemanticVerifier` / `EmbeddingPort` implementations reuse
the benchmark service's provider/input-keyed caches. Registration never enables a paid provider.

## Pipeline stages and checks

`ports\workflow.py` declares `PipelineStage`, `KnowledgeCheck`, execution/resolution/issue repositories
and typed contexts/results. `ProcessorSpec` declares identity, version, dependencies, input/output
contracts, capability/evidence requirements and configuration schema.

Register stages explicitly with `StageRegistry.register(stage)` and inject the registry through
`Runtime.orchestrator(registry)`. A replacement stage must preserve source evidence and return an
`ArtifactReference` compatible with its dependants. Stages run in dependency order; partial runs
require current successful compatible upstream artifacts. No external scheduler is introduced.

Add a check to a stage's `checks` tuple. `stages.enrichment.Check` wraps an async evaluation and an
optional typed `parameters` model; custom implementations may implement `KnowledgeCheck` directly.
Check dependencies must be registered in execution order; invalid/disabled prerequisites fail
validation. Version/provider/configuration changes trigger targeted check replay. Relevant evidence
determines proposal identity, preserving human decisions across unrelated corpus or check changes.
Checks needing current or historical alias groups call `ekb.domain.resolution.active_aliases` with
the snapshot and date; it derives edges from findings and effective accepted decisions.
See `phase3.md` for a concrete example and configuration.

## Repositories, artifacts and future adapters

`UnitOfWork` owns source, knowledge, lineage, processing, workflow, resolution and issue
repositories. Processing task inspection returns bounded pages in UUID order with an exclusive
`after` cursor. Explicit commits are required. Never return ORM rows outside the PostgreSQL adapter.
A future Neo4j implementation must pass the same contract tests and preserve
transaction/checkpoint/evidence semantics; the relational design does not require Neo4j.

`ArtifactStore` uses SHA-256 keys, idempotent writes and verified reads. Separate `put_named` /
`get_named` operations maintain atomic corpus views and checkpoints; consumers validate content hashes.
Named keys are safe relative object paths, never absolute filesystem paths. The Azure adapter uses
asynchronous clients and `DefaultAzureCredential`; provision the private container beforehand and
grant the identity only needed blob permissions. No Azure resource provisioning happens on scan.

Future ArchiMate exchange and Bizzdesign integrations should map original element identifiers
to stable source references without inventing evidence. `DeviationReference` reserves a typed
association to a specific concept revision and scope. `Deviation` and
`ResolutionRepository.deviations(as_of)` support an independently replaceable approved-deviation
count detector. The default repository returns no approved exceptions; approval/storage lifecycle
management remains out of scope. The synthetic demonstration exercises the detector explicitly.

## Verification

Repository contracts run against a disposable PostgreSQL schema. Artifact contracts include a real
local filesystem and a mocked Azure SDK client, with no cloud credentials. Entry-point discovery,
format parsing, HTTP conditional requests, native/fallback model output, all inspection transports,
resume, cancellation and ownership fencing have automated tests.
