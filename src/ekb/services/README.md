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
- `architectures.py` registers immutable solution artefacts, lists pinned requirement versions and
  traceability chains, and retains assessment reports per architecture revision.
- `solution_assessment.py` plans an approved baseline against design JSON and verifies ARM/Bicep
  through the `ImplementationAnalyzer` port; shared by CLI `assess`, API and MCP.
- `portfolio.py` derives read-only exception-risk and change-impact views from those plans.
- `assessment_basis.py` resolves a selected baseline independently of the design checklist;
  canonical evidence must bind its approval to the exact policy artifact and requirement versions.

Keep queries behind unit-of-work/repository contracts, returning domain values rather than ORM rows.
Do not silently expand bounded inspection into whole-corpus analytics.
See `tests\integration\test_interfaces.py` and `tests\unit\test_sources.py`.
