# Shared application services

- `knowledge.py` exposes concept, provenance, lineage, related-requirement and usage queries.
- `retry.py` retries transient HTTP failures with bounded exponential backoff and jitter.
- `benchmarks.py` materializes deterministic requirement candidates from structured publisher records.
  Preserve complete oversized statements in records rather than truncating their qualifiers.
- `reconciliation.py` shares control, historical comparison and evidence-chain queries.
  Published identifier mappings remain distinct from review-only semantic/version proposals.
- CLI, terminal, web and MCP presentation reuse these services.
- `knowledge_resolver.py` strictly resolves accepted identities, scope, dates and canonical authority.
- `reconciliation_service.py` and `issue_service.py` share audited decisions and issue lifecycle.

Keep queries behind unit-of-work/repository contracts, returning domain values rather than ORM rows.
Do not silently expand bounded inspection into whole-corpus analytics.
See `tests\integration\test_interfaces.py` and `tests\unit\test_sources.py`.
