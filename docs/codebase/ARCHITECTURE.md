# Architecture

## 1) Architectural Style

An async, layered modular monolith with ports and adapters. `Runtime` explicitly composes concrete
providers; application services and ingestion use typed protocols and frozen Pydantic values.
The primary constraints are exact provenance, incremental/restartable processing and bounded work.
See `docs\architecture.md` for the detailed component, data, execution and deployment design.

The pipeline's import of the source adapter's `SourceMissingError` is a remaining concrete coupling.
This is not a distributed graph-processing or autonomous-agent system.

## 2) System Flow

1. Typer's `scan` enters `Runtime` and constructs filesystem/manifest connectors.
2. `IngestionPipeline.scan` streams discovered items into bounded source workers.
3. `_process` compares revisions/fingerprints, preserves originals and claims durable work.
4. `_execute` reuses checkpoints or invokes parsing and bounded inference.
5. Exact evidence, interpretations, relationships, usage and replay checkpoints commit together.
6. Interfaces inspect persisted domain values through shared services; provenance is a database
   query, not a model-generated explanation.

## 3) Layer/Module Responsibilities

| Layer/module | Owns | Must not own | Evidence |
|---|---|---|---|
| `domain` | Schemas, identity, scope, grounding | Infrastructure SDKs | Domain models and grounding |
| `ports` | Typed provider/repository contracts | Concrete construction | `src\ekb\ports\__init__.py` |
| `pipeline`, `services` | Ingestion and inspection | ORM row representation | Pipeline and knowledge service |
| `adapters` | Sources, parsing, inference, artifacts and PostgreSQL | UI workflows | `src\ekb\adapters` |
| `interfaces` | Transport validation/presentation | Independent domain implementations | `src\ekb\interfaces` |
| `Runtime` | Provider selection, ownership and teardown | Implicit business-state lookup | `src\ekb\runtime.py` |

## 4) Reused Patterns

| Pattern | Where | Purpose |
|---|---|---|
| Ports/adapters and explicit composition | `ports`, `adapters`, `Runtime` | Replace infrastructure behind contracts |
| Repository/unit of work | PostgreSQL adapter | Explicit atomic persistence and rollback |
| Producer/consumer and stage gates | Pipeline | Bounded downloads, parsing and inference |
| Content addressing/deterministic identity | Domain helpers and artifacts | Idempotence and stable historical references |
| Leases and durable replay | Processing repository and pipeline | Coordinate workers and recover committed progress |
| Shared query service | `KnowledgeService` | Consistent inspection across transports |

## 5) Known Architectural Risks

Model calls and database commits are at-least-once, not exactly-once billing. Artifacts and database
commits are not one transaction. Inventory queries and per-result concept hydration grow with corpus
size despite bounded processing concurrency. Enterprise authentication, automatic promotion,
semantic reconciliation and the phase-two benchmark model are not implemented. Generalized
publication evidence, SemVer prereleases and scoped authority are supported independently.
See `docs\codebase\CONCERNS.md`.

## 6) Evidence

- `src\ekb\interfaces\cli\__init__.py`: `scan`, `init`, `worker`.
- `src\ekb\runtime.py`, `src\ekb\pipeline\__init__.py`, `src\ekb\services\knowledge.py`.
- `src\ekb\domain\models.py`, `src\ekb\domain\grounding.py`, `src\ekb\ports\__init__.py`.
- `src\ekb\adapters\persistence\postgres\__init__.py`,
  `src\ekb\adapters\persistence\postgres\models.py`.
- `docs\decisions\0001-provenance-and-transactions.md`,
  `docs\decisions\0002-runtime-and-dependencies.md`.
