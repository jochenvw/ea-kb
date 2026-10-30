# Shared application services

- `knowledge.py` exposes concept, provenance, lineage, related-requirement and usage queries.
  Search forwards source/level-filtered UUID-cursor pages without loading the complete corpus.
  Usage summarizes at most 1,000 tasks and retains unknown cost if any task is unpriced.
- `assertions.py` phrases existing versions with bounded model workers and replay caching;
  it never changes original candidates, occurrences, identities or authority.
- CLI, terminal, web and MCP presentation reuse these services.
- `knowledge_resolver.py` strictly resolves accepted identities, scope, dates and canonical authority.
- `reconciliation_service.py` and `issue_service.py` share audited decisions and issue lifecycle.
- `retrieval.py` supplies validated cosine-neighbor retrieval shared by enrichment and evals;
  scaling avoids overflow and underflow for finite nonzero embedding vectors.

Keep queries behind unit-of-work/repository contracts, returning domain values rather than ORM rows.
Do not silently expand bounded inspection into whole-corpus analytics.
See `tests\integration\test_interfaces.py` and `tests\unit\test_sources.py`.
