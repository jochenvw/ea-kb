# PostgreSQL repositories

- `models.py` declares relational constraints/indexes and JSONB domain payloads.
- `concept_versions.semantic_type` and relationship kinds are SQL-constrained to OWL identifiers;
  type projections must match payloads. Unknown stays null; succession is lifecycle metadata.
- `__init__.py` implements source, knowledge, lineage and processing repositories.
- Knowledge search uses exclusive UUID cursors and at most 200 concepts per query.
- Source filters and text search match the same evidenced version, not an unrelated latest one.
- `source_groups.py` resolves edition-level source IDs to original document IDs without rewriting rows.
- Level filters use that same version; `implementation_graph.py` joins current, exact native mappings.
- `concept_versions.assertion` is a replaceable phrasing cache; original version payloads stay immutable.
- `Postgres` creates units of work; uncommitted changes roll back on exit.
- `reset` locks and clears only EKB tables transactionally; active scans/leases block fresh starts.
- Knowledge counts query persisted totals without loading the complete knowledge snapshot.
- `inspection.py` joins source-bound extractions, bounded graphs/backlog and linked table previews.
- Processing claims use owner IDs and renewable leases; segment commits must fence stale workers.
- Committed task usage retains model-call counts; one unpriced contribution makes total cost unknown.
- Publication assessments are immutable; semantic versions may be unknown and authority is scoped.
- `benchmarks.py` stores immutable typed benchmark snapshots and validates exact source bindings.
- `documents(control_id=...)` selects matching whole documents, not a trimmed control list.
- Native references resolve only against exact namespace, revision and published OSCAL label aliases.
- Review-only comparisons validate source bindings; embeddings and valid judgments have replay caches.
- `workflow.py` fences stage/check runs and atomically applies decisions/authority; alias edges derive
  from immutable findings and accepted decision heads, not a separately persisted projection.
- `issues.py` prioritizes scoped issues before limiting and preserves review/reopening history.

Preserve immutable revisions, exact occurrences and idempotent checkpoints.
Before v1, `initialize` creates current ORM tables and `reset(recreate=True)` rebuilds owned tables
under existing reset locks/lease guards. From v1, schema changes require migrations. Verification starts with
`tests\contract\test_repositories.py` against disposable schemas.
