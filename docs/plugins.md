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

Future SharePoint support should add durable connector-level cursor storage and use Microsoft
Graph delta URLs and a credential abstraction. Do not equate an inaccessible page with deletion.
No SharePoint/Graph credentials, proprietary integrations, or fabricated remote documents ship here.

## Parsing and inference

`DocumentParser` returns stable segments with revision IDs, hierarchy, locations, quality and media
references, with no model calls. Parser configuration must change its fingerprint whenever output
may change. Preserve originals; normalized text is an intermediate artifact, not a substitute.

`InferencePort` exposes capabilities, prompt version and validated structured extraction.
Use explicit model configuration; do not assume every endpoint supports native JSON schemas,
vision, embeddings or a particular context length. Adapter output is validated against Pydantic
and exact source evidence before persistence. Inference has bounded concurrency, transient retries,
bounded schema retries and replayable results. The supplied optional Agent Framework adapter uses
a chat client, not an autonomous agent.

Extraction currently also proposes directly evidenced local relationships. No separate relationship,
deduplication or embedding model is invoked unnecessarily; introduce task-specific providers when
those deferred tasks have evaluated implementations.

## Repositories, artifacts and future adapters

`UnitOfWork` owns small source, knowledge, lineage and processing repositories. Explicit commits
are required. Never return ORM rows outside the PostgreSQL adapter. A future Neo4j implementation
must pass the same contract tests and preserve transaction/checkpoint/evidence semantics; the
relational design does not require Neo4j.

`ArtifactStore` uses SHA-256 keys, idempotent writes and verified reads. Separate `put_named` /
`get_named` operations maintain atomic corpus views and checkpoints; consumers validate content hashes.
Named keys are safe relative object paths, never absolute filesystem paths. The Azure adapter uses
asynchronous clients and `DefaultAzureCredential`; provision the private container beforehand and
grant the identity only needed blob permissions. No Azure resource provisioning happens on scan.

Future ArchiMate exchange and Bizzdesign integrations should map original element identifiers
to stable source references without inventing evidence. `DeviationReference` reserves a typed
association to a specific concept revision and scope. Deviation approvals, counts and lifecycle
management remain out of scope.

## Verification

Repository contracts run against a disposable PostgreSQL schema. Artifact contracts include a real
local filesystem and a mocked Azure SDK client, with no cloud credentials. Entry-point discovery,
format parsing, HTTP conditional requests, native/fallback model output, all inspection transports,
resume, cancellation and ownership fencing have automated tests.
