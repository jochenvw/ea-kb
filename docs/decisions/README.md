# Architecture decisions

- `0001-provenance-and-transactions.md`: exact evidence, identity, checkpoints and transaction rules.
- `0002-runtime-and-dependencies.md`: async monolith, deterministic parsing and provider choices.
- `0003-publication-and-semver.md`: maturity evidence, unknown versions and scoped authority.
- `0006-browser-presentation.md`: actual shadcn, information hierarchy, local assets and browser checks.
- `0007-bounded-ingestion-skills.md`: deterministic inventory, separate semantic skills and precision-gated frugality.
- `0008-ontology-ubiquitous-language.md`: authoritative vocabulary, read-only viewer and language checks.
- `0009-pre-v1-development-state.md`: version registration, disposable development state and schema rebuilds.
- `0010-pydantic-evals.md`: standard skill-evaluation runner, bounded cases and durable performance history.
- `0011-derived-alias-edges.md`: derive reversible aliases from findings and retain distinct scan records.
- `0012-shared-source-ingestion.md`: ordinary source ingestion and guarded legacy-table cleanup.

Read these before changing persistence invariants, execution boundaries or major dependencies.
Record a new significant decision briefly; keep detailed implementation description in
`docs\architecture.md` and link to the relevant contracts or tests.
