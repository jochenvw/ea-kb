# Build: Enterprise Knowledge Base (EKB)

## Role and mission

You are a senior Python architect and AI engineer. Build the initial working implementation of **Enterprise Knowledge Base (EKB)**: a multimodal, incrementally indexed, provenance-aware knowledge system for enterprise architecture and governance.

The system ingests strategy documents, industry standards, regulatory publications, architecture models, and eventually internal enterprise documents.

It extracts two fundamental semantic primitives:

1. **Objective** — A desired business, operational, technical, or governance outcome.
2. **Requirement** — A mandated or recommended condition, rule, constraint, or practice.

It establishes relationships between these primitives, preserves the exact evidence supporting them, and lays the foundation for canonical versioning, architecture impact analysis, deviation management, and Model Context Protocol (MCP) exposure.

**Core philosophy: lightweight indexing first, progressive semantic enrichment later.**

Do not build a heavyweight GraphRAG pipeline. Do not extract everything into an exhaustive knowledge graph on first ingestion. Minimize unnecessary model calls and reprocessing.

Deliver production-quality foundations and an executable MVP, not a collection of speculative abstractions.

---

## 1. Architectural principles

### 1.1 Modular monolith with ports and adapters

Use hexagonal architecture.

- Domain logic must not depend on database libraries, web frameworks, model SDKs, cloud SDKs, or UI frameworks.
- Define ports using Python `Protocol` interfaces.
- Implement adapters for providers and infrastructure.
- Construct adapters through explicit dependency injection.
- Avoid a dependency-injection framework unless demonstrably necessary.
- Plugins should be discoverable through Python package entry points where appropriate.
- Keep interfaces task-oriented and small.

We want interchangeable implementations for:

- Knowledge persistence: PostgreSQL initially; Neo4j later.
- Artifact storage: local filesystem or Azure Blob Storage.
- Source discovery: local filesystem, HTTP, and later SharePoint, Azure Blob, and APIs.
- Model inference: Microsoft Foundry in Azure, Foundry Local, and other supported providers.
- User interface: CLI/TUI locally, web UI in Azure, and MCP.
- Telemetry export: local disk or Azure Application Insights.

Do not implement speculative backend adapters merely to demonstrate extensibility.

### 1.2 Async-first execution

Use modern asynchronous Python throughout I/O-heavy operations.

- Python 3.13, unless a dependency compatibility issue justifies an alternative.
- `asyncio.TaskGroup` for structured concurrency.
- Bounded concurrency and backpressure.
- Async HTTP requests and database sessions.
- Configurable concurrency limits per pipeline stage.
- Retry transient failures with exponential backoff and jitter.
- Support graceful shutdown, cancellation, and restart.

Avoid unbounded parallelism, especially for OCR, local inference, and remote model endpoints.

### 1.3 Incremental and idempotent

Repeated scans of unchanged sources must not trigger unnecessary parsing, OCR, embedding, or LLM calls.

Use source-specific revision indicators, content hashes, processing fingerprints, and durable checkpoints.

Processing must be recoverable after crashes or interruptions.

### 1.4 Provenance by design

Every extracted concept and relationship must link to the evidence supporting it.

Preserve original documents and source identifiers.

Never create an authoritative requirement solely because an LLM inferred one.

Distinguish:

- Explicit source statements.
- Proposed semantic interpretations.
- Verified relationships.
- Canonically approved knowledge.

---

## 2. Technology stack

Use:

| Area | Technology |
|---|---|
| Runtime | Python 3.13 |
| Package management | uv |
| Packaging | pyproject.toml and src layout |
| Type checking | basedpyright, strict where practical |
| Linting / formatting | Ruff |
| Domain schemas | Pydantic v2 |
| Configuration | pydantic-settings |
| CLI | Typer |
| TUI | Textual |
| API / web backend | FastAPI |
| Initial web frontend | Jinja2 + HTMX |
| Database | PostgreSQL |
| ORM / queries | SQLAlchemy 2 async + asyncpg |
| Migrations | Alembic |
| Vector search | pgvector, added when needed |
| HTTP | HTTPX |
| Document extraction | Docling, PyMuPDF and format-specific parsers |
| AI integration | Microsoft Agent Framework behind an inference port |
| MCP | Official MCP Python SDK / FastMCP |
| Observability | OpenTelemetry, structlog |
| Azure telemetry | Azure Monitor / Application Insights exporter |
| Testing | pytest, pytest-asyncio, Hypothesis |
| Containers | Docker and Docker Compose |

Use current compatible stable library versions. Verify the actual package names, supported Python versions, API signatures, and available model capabilities instead of assuming them.

Microsoft Agent Framework is approved as a dependency, but do not use autonomous agents for tasks that are better implemented deterministically.

Keep LLM execution behind a provider-independent interface.

---

## 3. Initial semantic model

Keep the business ontology deliberately small.

### 3.1 Concept primitives

**Objective**

Represents something an organization, regulator, or other authority wants to achieve.

Examples:

- Reduce operational costs.
- Improve cybersecurity resilience.
- Reduce emissions.
- Increase LNG sales.
- Improve application availability.

**Requirement**

Represents a condition, obligation, recommendation, or architectural constraint.

Examples:

- Public endpoints require security protection.
- Implement appropriate network boundary controls.
- Establish information security governance.
- Use approved encryption mechanisms.

Requirements have an applicability scope and a modality.

Modality:

- `mandated`
- `recommended`
- `unspecified`

Do not equate the wording in an external document with its binding status for a particular enterprise. A NIST recommendation may become mandatory through an internal decision. A regulation can impose a requirement independently.

Store the original wording separately from the interpreted modality and authority.

### 3.2 Relationships

Initially support:

- `DERIVES_FROM`
- `SUPPORTS`
- `DEPENDS_ON`
- `OVERLAPS_WITH`
- `TENSION_WITH`
- `CONFLICTS_WITH`

Relationships must preserve direction where applicable, evidence, scope, and verification status.

Use `SUPERSEDES` for version lineage separately from business semantics.

A relationship can be proposed without being accepted as authoritative.

### 3.3 Scope

Represent scope as structured metadata rather than forcing it into a complex ontology.

Support optional dimensions such as:

- Organization
- Business unit
- Geography / jurisdiction
- Technology platform
- Application or solution
- Environment
- Effective period

Allow unknown or incompletely extracted scope.

Do not invent values.

### 3.4 Canonical knowledge and versions

Design persistence for:

- Stable concept identities.
- Immutable concept revisions.
- Semantic versions: MAJOR.MINOR.PATCH.
- Historical source occurrences.
- Canonical promotion and withdrawal events.
- Effective dates and recorded dates.
- Explicit predecessor and successor references.

Version semantics:

- PATCH: Editorial or nonsemantic clarification.
- MINOR: Additive, backward-compatible change.
- MAJOR: Breaking or incompatible change affecting existing interpretations or implementations.

A document revision does not automatically imply a new semantic concept version.

For the MVP, implement the structural foundation and basic version history. Do not build autonomous canonical promotion or automatic semantic version classification yet.

---

## 4. Persistence architecture

Use PostgreSQL as the default authoritative store.

Do not require Neo4j or another graph database.

Use relational tables for nodes and edges. Support recursive lineage traversal through SQL.

Recommended initial entities:

- `sources`
- `source_revisions`
- `segments`
- `concepts`
- `concept_versions`
- `occurrences`
- `relationships`
- `authority_events`
- `processing_runs`
- `processing_tasks`

Also reserve a clean extension point for architecture deviation and exception records.

All identifiers must be stable, including when the same source is rediscovered.

Use database constraints to prevent duplicate revisions, accidental duplicate processing, and inconsistent references.

Separate source revision identity from processing revision identity.

Example processing fingerprint:

`hash(source_content_hash, parser_version, schema_version, model_id, prompt_version, extraction_config)`

A change in parser or model configuration must support intentional reprocessing without pretending the source itself changed.

### 4.1 Repository ports

Define small typed interfaces, including:

- `KnowledgeRepository`
- `SourceRepository`
- `LineageRepository`
- `ProcessingRepository`
- `UnitOfWork`

Keep SQLAlchemy models inside the PostgreSQL adapter.

Do not leak ORM entities into domain or service layers.

Expose graph-oriented operations such as `trace_origins()` and `find_dependents()` instead of generic database query execution.

Design transaction semantics explicitly.

Implement only the PostgreSQL adapter now.

### 4.2 Artifact storage

Define an `ArtifactStore` port.

Implement:

- Filesystem artifact store.
- Azure Blob artifact store.

Store original documents and intermediate artifacts by stable, content-addressed keys where practical.

Keep source location separate from artifact location.

---

## 5. Source connector architecture

Define a `SourceConnector` interface that supports:

- Discovering available items.
- Identifying stable source IDs.
- Reading modification indicators.
- Fetching changed content.
- Tracking incremental cursors or checkpoints.
- Reporting source metadata.

Initial working connectors:

1. Local filesystem — PDF, DOCX, PPTX, TXT, Markdown.
2. HTTP — Download publicly accessible source documents with conditional requests.
3. NIST OSCAL — Import machine-readable catalogs where available.

Define plugin interfaces and configuration for future:

- Microsoft SharePoint / Microsoft Graph delta discovery.
- Azure Blob Storage.
- External architecture repositories, including Bizzdesign and ArchiMate exchange files.
- Other regulatory APIs.

Do not implement unavailable proprietary integrations or fabricate example documents.

SharePoint integration should eventually use Microsoft Graph delta capabilities and an appropriate credential abstraction.

---

## 6. Initial source corpus

Provide a reproducible source manifest containing publicly available and legally retrievable enterprise strategy, standards and regulatory material. The initial corpus is illustrative, not a company or industry restriction.

Initial targets:

**Corporate strategy**

- A public corporate annual report.
- Public corporate strategy or sustainability material.

**Cybersecurity**

- NIST Cybersecurity Framework 2.0.
- Selected machine-readable NIST SP 800-53 controls.

**Industry standards**

- ISO/IEC 27001 metadata and permitted publicly available material.
- ISO 14001 metadata and permitted publicly available material.

**Regulatory**

Choose a small, clearly identified set of regulatory publications. The initial corpus uses offshore safety examples; other industries can supply their own manifests. Include jurisdiction and applicability metadata.

Possible domains include operational safety, environmental protection, cybersecurity, and industrial control systems.

Do not assume every regulation applies to every enterprise or operation.

Do not download or redistribute copyrighted ISO full texts without suitable authorization. Provide a mechanism for users to supply licensed documents locally.

For each source manifest entry, include:

- Publisher
- Source name
- Stable identifier
- Official URL where applicable
- Document format
- Source category
- Jurisdiction, if known
- Retrieval method
- Licensing or access notes
- Intended scope

Support a small local fixture corpus so tests run without internet access.

---

## 7. Ingestion pipeline

Implement the following stages.

### Stage A — Discovery

Run independent connector discovery operations concurrently, with bounded concurrency.

Detect new, changed, unchanged, missing, and inaccessible sources.

Use metadata revision signals before downloading content where supported.

Persist discovery results and checkpoints.

### Stage B — Change detection

Use:

- HTTP ETag / Last-Modified where trustworthy.
- File metadata as an inexpensive early filter.
- SHA-256 content hashes for authoritative content comparison.
- Source revision identifiers.
- Processing fingerprints.

Unchanged source revisions with matching processing fingerprints must be skipped.

### Stage C — Parsing

Prefer structured extraction before OCR.

Support:

- PDF text, headings and page references.
- DOCX headings, paragraphs and tables.
- PPTX slides, text boxes and notes where available.
- Plain text and Markdown.
- OSCAL structured records.

Preserve document structure and reading order as far as practical.

Use OCR selectively for pages without reliable embedded text.

Do not OCR every document by default.

Preserve images or diagram references for later multimodal processing.

Parsing must be independently testable without any model invocation.

### Stage D — Lightweight semantic segmentation

Identify semantic sections rather than blindly chunking every N tokens.

Preserve parent document and section hierarchy.

Avoid exhaustive fact extraction during parsing.

Produce normalized segments with stable IDs, text, structure, location, and extraction quality indicators.

### Stage E — Semantic extraction

Use a configurable small language model to identify candidate objectives and requirements.

Return validated structured outputs.

For each extracted concept, include:

- Type.
- Concise statement.
- Original supporting excerpt.
- Exact source reference.
- Scope.
- Modality, if applicable.
- Confidence or uncertainty indicators.
- Extraction metadata.

Do not infer missing details as facts.

Ensure the supporting text actually exists in the source.

Persist candidates independently of canonical approval.

### Stage F — Basic reconciliation

Initially implement:

- Exact duplicate detection.
- Source-level and segment-level change tracking.
- Candidate semantic duplicate retrieval.
- Basic relationship proposals where directly supported by evidence.

Do not automatically merge concepts because their embeddings are similar.

Prefer explicit identifiers and structured source references over inferred semantic matches.

Defer expensive cross-corpus relationship discovery until the foundational pipeline is evaluated.

---

## 8. AI model architecture

Use Microsoft Agent Framework where useful, but protect application code from provider-specific dependencies.

Define an `InferencePort` with structured extraction and capability metadata.

Support configurations for:

- Microsoft Foundry hosted models.
- Foundry Local.
- OpenAI-compatible model serving.
- Other providers through additional adapters.

Allow different models for different tasks:

- Extraction.
- Relationship classification.
- Deduplication.
- Embeddings.

The initial model should be as small and inexpensive as possible while meeting evaluation thresholds.

Do not hardcode a supposedly optimal model.

Implement capability-aware behavior:

- Native structured output when supported.
- Pydantic validation.
- Bounded validation retries.
- Clear error reporting.
- Token and latency accounting.

Avoid autonomous tool loops in the initial extraction pipeline.

Model calls should be independently replayable and evaluable.

---

## 9. Observability and debugging

OpenTelemetry is mandatory throughout the application.

### Local

- Structured JSONL logs on disk.
- OpenTelemetry traces exported to local files through a supported collector/exporter configuration.
- Local trace visualization optional.
- Configurable retention and rotation.

### Azure

- OpenTelemetry exported to Azure Monitor / Application Insights.
- Trace correlation across API requests, workers, model calls and persistence.

Use the same instrumentation in both environments.

### Required tracing

Create trace spans for:

- Scan execution.
- Source discovery.
- Revision comparison.
- Document download.
- Parsing.
- OCR.
- Segmentation.
- Model inference.
- Schema validation.
- Concept persistence.
- Relationship creation.
- Retry and error handling.

Include appropriate identifiers:

- Scan ID.
- Source ID.
- Source revision ID.
- Segment ID.
- Model and prompt versions.
- Concept IDs.
- Processing task IDs.

Track model tokens, latency, cost estimates where pricing is configured, and validation failures.

Provide an optional restricted debug-artifact mode that records model input/output payloads separately from telemetry, with trace references.

Never include entire confidential documents or secrets in telemetry by default.

Logs and traces should enable an engineer or coding agent to diagnose why a specific concept was extracted, skipped, duplicated, or rejected.

---

## 10. Interfaces

### CLI

Implement Typer commands including:

- `ekb init`
- `ekb sources list`
- `ekb scan`
- `ekb scan --dry-run`
- `ekb scans list`
- `ekb concepts list`
- `ekb concepts show <id>`
- `ekb lineage <id>`
- `ekb eval run`
- `ekb doctor`

The commands must work without launching a web server.

### TUI

Build a lightweight Textual interface with:

- Source inventory.
- Last scan status.
- Start/resume scan.
- Per-source progress.
- Processing failures.
- Extracted objective and requirement browser.
- Concept detail and provenance.
- Basic relationship inspection.
- Model usage and processing statistics.

Keep the interface responsive while scans execute asynchronously.

### Web

Build a minimal FastAPI web interface using server-rendered templates and HTMX.

Implement the same core workflows as the TUI.

Avoid a large JavaScript frontend or separate Node.js build system for the MVP.

### MCP

Keep the knowledge service independent of transport.

Expose a minimal read-only MCP server with tools such as:

- `search_concepts`
- `get_concept`
- `trace_provenance`
- `find_related_requirements`

Use explicit input and output schemas.

Do not implement an autonomous compliance checker in the MVP.

Later, an agent should be able to trace:

General security requirement → Azure architecture standard → solution decision → implementation evidence.

The architecture must also eventually support approved deviations and queries such as identifying guidelines with more than ten active exceptions.

These are future capabilities and should inform the data model, not expand MVP scope unnecessarily.

---

## 11. Testing and evaluation

This project must be testable without requiring cloud credentials or paid model inference.

### Unit tests

Cover:

- Change detection.
- Content fingerprinting.
- Incremental scan logic.
- Scope and modality validation.
- Concept schema validation.
- Semantic version representation.
- Source and evidence references.
- Connector error handling.

### Integration tests

Use disposable PostgreSQL for persistence tests.

Verify:

- A second unchanged scan performs zero parsing and model extraction.
- A changed source creates the correct revision.
- A failed processing task can resume.
- Concurrent workers do not duplicate committed work.
- Provenance links resolve to real source segments.
- Recursive lineage traversal works and handles cycles.
- Document deletion or temporary inaccessibility does not silently erase historical evidence.

Use backend contract tests for repository interfaces.

### Evaluation dataset

Create a small, manually reviewable golden dataset with:

- Annual-report objectives.
- NIST recommendations and controls.
- ISO-related requirements where authorized text exists.
- Regulatory mandates.
- Statements containing qualifiers, exceptions, and negations.
- Documents containing several unrelated objectives or requirements.
- Semantically similar but non-equivalent statements.

Evaluate:

- Objective precision and recall.
- Requirement precision and recall.
- Modality accuracy.
- Scope accuracy.
- Evidence grounding.
- Duplicate detection quality.
- Cost and latency per document.

Penalize falsely inventing mandatory requirements heavily.

Compare a small candidate model against a stronger baseline when model access is available.

Do not fabricate benchmark results.

Produce a machine-readable evaluation report and a concise human-readable summary.

---

## 12. Project structure

Use a structure similar to:

```text
enterprise-knowledge/
├── pyproject.toml
├── uv.lock
├── README.md
├── .env.example
├── Dockerfile
├── compose.yaml
├── src/ekb/
│   ├── domain/
│   ├── ports/
│   ├── services/
│   ├── pipeline/
│   ├── adapters/
│   │   ├── sources/
│   │   ├── parsers/
│   │   ├── artifacts/
│   │   ├── inference/
│   │   └── persistence/
│   │       └── postgres/
│   ├── interfaces/
│   │   ├── cli/
│   │   ├── tui/
│   │   ├── api/
│   │   ├── web/
│   │   └── mcp/
│   ├── observability/
│   ├── evaluation/
│   └── config/
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── golden/
├── config/
│   ├── local.yaml
│   └── azure.yaml
├── sources/
│   └── public.yaml
├── deploy/
│   ├── local/
│   └── azure/
└── docs/
    ├── architecture.md
    └── decisions/
```

Keep runtime-generated files outside the source tree or inside gitignored data directories.

Provide clear commands for initial setup, database migration, local execution, testing, evaluation, and container execution.

---

## 13. Deployment profiles

### Local development

- Python app on the host.
- PostgreSQL via Docker Compose.
- Filesystem artifact storage.
- Local source directories and HTTP sources.
- Textual TUI and CLI.
- Foundry Local or configured model endpoint.
- OpenTelemetry to local disk.
- No mandatory Azure dependencies.

### Azure

- Containerized FastAPI and web UI.
- Separate containerized background worker.
- Azure Database for PostgreSQL.
- Azure Blob Storage.
- Managed identity where supported.
- Microsoft Foundry model endpoints.
- Azure Application Insights.
- Later: Microsoft Graph / SharePoint connectors.

Provide a minimal containerization and configuration foundation now. Full production Azure infrastructure provisioning, authentication hardening, network isolation, and deployment automation can follow in a separate milestone.

Avoid building a distributed microservices system.

---

## 14. Implementation sequence

Work incrementally and keep the application executable after each milestone.

**Milestone 1 — Foundation**

Create the package, domain models, ports, PostgreSQL adapter, migrations, configuration, CLI skeleton, observability, and test infrastructure.

**Milestone 2 — Deterministic ingestion**

Implement filesystem and HTTP connectors, revision detection, artifact storage, PDF/DOCX/PPTX extraction, checkpoints, and restartable workers.

Demonstrate that unchanged documents are skipped.

**Milestone 3 — Knowledge extraction**

Implement the inference port, one working model adapter, typed objective/requirement extraction, evidence validation, persistence, and the initial evaluation suite.

**Milestone 4 — Inspection interfaces**

Implement the TUI, minimal web UI, provenance queries, and read-only MCP tools.

**Milestone 5 — Initial public corpus and validation**

Ingest a small representative set of accessible corporate strategy, NIST, ISO-related, and regulatory material.

Produce extraction results with source evidence and evaluation metrics.

**Milestone 6 — Extensibility foundation**

Complete the Azure Blob adapter, basic Azure profile, plugin registration tests, and documented contracts for future SharePoint, ArchiMate, Bizzdesign and Neo4j integrations.

Do not build those future integrations unless explicitly requested.

---

## 15. Definition of done

The initial application is complete when:

1. A developer can clone the repository, run `uv sync`, start PostgreSQL, and initialize the database.
2. A local directory of PDF, Word, and PowerPoint documents can be scanned.
3. At least one official machine-readable NIST source can be imported.
4. Objective and requirement candidates are extracted and stored with exact evidence references.
5. Repeating the scan without changes makes no unnecessary model calls.
6. A changed document is selectively reprocessed with version history preserved.
7. Source provenance and relationship records can be inspected through the CLI and TUI.
8. A minimal web UI can display the same information.
9. Read-only MCP queries return structured concepts and provenance.
10. OpenTelemetry traces and structured logs can be inspected locally.
11. The same instrumentation can export to Application Insights through Azure configuration.
12. Tests and evaluation commands run reproducibly.
13. Documentation explains the architecture, deployment modes, plugin contracts, and known limitations.

Every major feature must have an automated verification path.

---

## 16. Engineering instructions

Before coding:

1. Inspect the repository and existing conventions.
2. Verify major library compatibility and current APIs.
3. Produce a concise implementation plan.
4. Identify the smallest working vertical slice.
5. Record important architectural choices as short Architecture Decision Records (ADRs).

Then implement, test, and iterate.

Do not stop after creating interfaces, directories, or placeholder implementations.

Prefer tested functionality over broad but unfinished scaffolding.

Use small, cohesive modules. Avoid generic base classes, unnecessary abstractions, massive service classes, or over-engineered plugin frameworks.

Implement only the abstractions needed to maintain the explicitly required portability boundaries.

When uncertain, favor simplicity, observability, correctness, and incremental processing efficiency.

**Most important constraint:** The knowledge base must always be able to explain where a statement came from. Semantic understanding can become progressively richer, but provenance must be correct from the very first ingestion.
