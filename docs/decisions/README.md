# Architecture decisions

- `0001-provenance-and-transactions.md`: exact evidence, identity, checkpoints and transaction rules.
- `0002-runtime-and-dependencies.md`: async monolith, deterministic parsing and provider choices.
- `0003-publication-and-semver.md`: maturity evidence, unknown versions and scoped authority.
- `0004-structured-benchmark-ingestion.md`: model-free records, exact bindings and replay boundaries.

Read these before changing persistence invariants, execution boundaries or major dependencies.
Record a new significant decision briefly; keep detailed implementation description in
`docs\architecture.md` and link to the relevant contracts or tests.
