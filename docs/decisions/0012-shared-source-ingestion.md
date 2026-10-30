# 0012: Shared ontology ingestion for publisher sources

Status: accepted.

All publisher documents enter through the ordinary source, parsing, labeling and extraction
contracts. Publisher names, titles, versions, URLs and licenses remain source facts, not domain
types. Explicit product/platform qualifiers are retained; unsupported layers and links remain unknown.
Source publication and review proposals never establish enterprise adoption.

This supersedes the removed source-specific ingestion and comparison decisions. During pre-v1
reset/recreation, four explicitly named historical EKB tables are removed only in the active schema
and without cascading. Active-work guards and transactional rollback apply; unrelated tables and
original artifacts are retained. The reset transaction restricts lookup to the current schema, so
missing tables cannot resolve to another schema on the search path. Current ORM metadata and
migrations do not recreate those tables.

Generic semantic checks and independently evaluated skills remain replaceable. Validated semantic
results are replayed independently of the check version when provider and inputs are unchanged.
One generic typed cache stores embeddings and grounded judgments, not publisher-specific projections.
Changed inputs can incur new model calls; exactly-once provider billing is not guaranteed. See
`docs\architecture.md` and `docs\plugins.md`.
