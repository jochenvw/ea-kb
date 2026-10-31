# PostgreSQL repositories

- `models.py` declares relational constraints/indexes and JSONB domain payloads.
- `concept_versions.semantic_type` and relationship kinds are SQL-constrained to OWL identifiers;
  type projections must match payloads. Unknown stays null; succession is lifecycle metadata.
- `__init__.py` implements source, knowledge, lineage and processing repositories.
- Knowledge search uses exclusive UUID cursors and at most 200 concepts per query.
- Source filters and text search match the same evidenced version, not an unrelated latest one.
- Level filters use that same evidenced version; graph edges require retained evidence or proposals.
- Provenance selectors resolve exact current-revision heading/excerpt matches; configured links keep
  relationship evidence separate from concept evidence and may use ontology `inDomain`.
- Graph inspection follows up to three relationship hops and shows source provenance for every
  retained concept in the bounded neighborhood.
- `concept_versions.assertion` is a replaceable phrasing cache; original version payloads stay immutable.
- `Postgres` creates units of work; uncommitted changes roll back on exit.
- `reset` locks and clears only EKB tables transactionally; active scans/leases block fresh starts.
- Scan guards reuse `pipeline_leases`; server-clock renewal and each guarded commit fence stale owners.
- Expired guarded reports permit reset; unguarded legacy running reports remain conservatively blocking.
- Knowledge counts query persisted totals without loading the complete knowledge snapshot.
- `inspection.py` joins source-bound extractions, bounded graphs/backlog and linked table previews.
- Processing claims use owner IDs and renewable leases; segment commits must fence stale workers.
- Committed task usage retains model-call counts; one unpriced contribution makes total cost unknown.
- Publication assessments are immutable; semantic versions may be unknown and authority is scoped.
- `workflow.py` fences stage/check runs and atomically applies decisions/authority; alias edges derive
  from immutable findings and accepted decision heads, not a separately persisted projection.
- Its `semantic_replay_cache` uses one content-derived primary key and typed embedding/judgment
  payloads. Invalid payloads are cache misses and valid replacements are committed independently.
- `initialize` creates this table if absent without resetting existing checkpoints or originals.
- `issues.py` prioritizes scoped issues before limiting and preserves review/reopening history.

Preserve immutable revisions, exact occurrences and idempotent checkpoints.
Before v1, `initialize` creates current ORM tables and `reset(recreate=True)` rebuilds owned tables
under existing reset locks/lease guards. Reset and recreation remove four explicitly named obsolete
benchmark tables and legacy `concept_aliases`. Cleanup stays in the active schema and never cascades
or deletes source inputs or filesystem/Azure originals. From v1, schema changes require migrations.
Verification starts with `tests\contract\test_repositories.py` against disposable schemas.
