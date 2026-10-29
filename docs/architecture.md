# Architecture

EKB is an asynchronous modular monolith. PostgreSQL is the authoritative store;
original documents live in content-addressed filesystem or Azure Blob artifacts.

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

`SUPERSEDES` is represented by version predecessor references, not mixed into business
relationships. Recursive origin/dependent queries traverse `DERIVES_FROM` and `DEPENDS_ON`;
SQL tracks visited paths and bounds depth to terminate cycles. Other relationship kinds are
available for inspection but are not treated as dependency lineage.

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

See the ADRs in `decisions` for the transaction and dependency choices.
