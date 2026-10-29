# Codebase Concerns

An implementation-limit/design-risk inventory through `9ea2a35`, not a penetration test or a claim
that every listed risk has produced a failure.

## 1) Top Risks (Prioritized)

| Priority | Concern | Evidence | Impact | Suggested action |
|---|---|---|---|---|
| High before external exposure | No enterprise authentication/authorization | API factory, README | Reachability grants knowledge/scan access | Keep private; add identity before shared deployment |
| High for policy use | Limited offline baseline; candidates are not approved policy | `RulesInference` | Incomplete/misclassified interpretations | Evaluate a model and retain review |
| Medium | Model completion and checkpoint commit are separate | Pipeline `_execute` | Crash can repeat billable calls | Preserve idempotence; allow for at-least-once calls |
| Medium | Separate artifact/database durability | Artifacts, pipeline `_process` | Orphans or incomplete restore | Retain/migrate both stores |
| Medium | Benchmark reconciliation absent | `goal2.md`, domain/ORM models | No benchmark extraction/comparison yet | Treat those requirements as planned behavior |

## 2) Technical Debt

| Item | Why/where | Risk | Suggested change when needed |
|---|---|---|---|
| Concrete adapter error in pipeline | `SourceMissingError` import | Source-provider coupling | Move shared failure semantics to a contract boundary |
| Adapter-level version insertion | `Knowledge.add_version`, port definitions | No provider-neutral user approval workflow | Design use case/contract before UI |
| Publication inference is conservative | `domain\publication.py` | Ambiguous/missing status remains unknown | Review cited evidence; keep authority explicit |
| Heuristic format reconstruction | `StructuredParser` | Complex columns/tables/diagrams can lose semantics | Extend using real examples and grounding regressions |

The source/config scan found no production `TODO`/`FIXME`/`HACK` markers. Their absence does not
mean these documented limits are implemented.

## 3) Security Concerns

| Risk | Evidence | Current mitigation | Gap |
|---|---|---|---|
| Unauthenticated endpoints | API, Compose | Localhost publishing, configured source paths, origin checks | Enterprise identity/authorization |
| Sensitive optional payloads | Inference `_debug` | Off by default, separate files, no auth headers | Operator-owned access/retention, especially Windows ACLs |
| Licensed-source restrictions | Manifests/downloader | ISO metadata-only; refused retrieval is not fabricated | Authorized private copies and applicability review |

These are documented operational boundaries; no exploit assessment was performed for this task.

## 4) Performance and Scaling Concerns

| Concern | Evidence | Current implementation | Scaling risk | Suggested improvement |
|---|---|---|---|---|
| Per-concept query hydration | `Knowledge.search`, `get` | Additional queries for each selected ID | Query count grows with result count | Batch/paginate after measurement |
| Complete inventories | `Sources.list`, pipeline `discover` | In-memory list and connector seen set | Inventory memory/transaction growth | Paginated inventory reconciliation when justified |
| Blocking parser cancellation | `StructuredParser.parse` | Threaded work | Cancellation cannot instantly stop parser | Keep concurrency bounded; measure before isolating processes |
| Query/statistics caps | Repositories and `KnowledgeService.statistics` | 200 search results, 1,000 tasks | Not whole-corpus analytics at scale | Explicit pagination/aggregation |

No production load benchmark or latency service-level objective is established. These are
code-derived scaling risks, not measured production incidents.

## 5) Fragile/High-Churn Areas

| Area | Why sensitive | Churn signal | Safe change strategy |
|---|---|---|---|
| PostgreSQL adapter | Largest handwritten Python module; repositories, leases, recursive lineage | Initially committed together | Preserve contracts and migrations |
| Pipeline/artifacts | Ownership, cancellation, replay and atomic publication interact | Initial implementation; concurrency regressions exist | Exercise concurrent claims, cancellation and writes |
| README/packaging | Workflow/tool integration changes | Two modifying commits each in the initial four-commit history | Keep commands aligned with tools |

Four initial commits are too few to infer historical fragility from churn counts. The removed
ZIP is not a source hotspot.

## 6) Intent-Dependent Questions

No `[ASK USER]` items are needed to describe the implementation. Deployment policy, service-level
targets and future phase-two choices are not inferred.

## 7) Evidence

- `goal.md`, `goal2.md`, `README.md`, `docs\architecture.md`.
- `src\ekb\pipeline\__init__.py`, `src\ekb\domain\models.py`.
- `src\ekb\adapters\persistence\postgres\__init__.py`,
  `src\ekb\adapters\persistence\postgres\models.py`.
- `src\ekb\adapters\artifacts\__init__.py`, `src\ekb\adapters\parsers\__init__.py`.
- `src\ekb\adapters\inference\__init__.py`, `src\ekb\interfaces\api\__init__.py`.
- `src\ekb\services\knowledge.py`, `compose.yaml`, `tests\unit\test_adapters.py`.
- Git history: `git log --since="90 days ago" --name-only` inspected at `9ea2a35`.
