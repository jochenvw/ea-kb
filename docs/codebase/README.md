# Codebase map

- [Architecture](../architecture.md): layers, composition, flows, persistence and known limits.
- [Application package](../../src/ekb/README.md): implementation ownership and local folder guides.
- [Coding standards](../../AGENTS.md) and [decisions](../decisions/README.md): conventions and rationale.
- [Usage](../usage.md): settings, integrations, deployment boundaries and verification.
- [Plugin contracts](../plugins.md): interchangeable adapters, stages and repositories.
- [Evaluations](../evaluation.md) and [tests](../../tests/README.md): quality gates and isolation.
- [Frontend](../../frontend/README.md): browser source, build and committed assets.
- Ingestion runs/status/resume are CLI-owned; browser/TUI retain knowledge and governance inspection.
- [Python manifest](../../pyproject.toml) and [frontend manifest](../../frontend/package.json):
  dependency declarations; their committed lockfiles are authoritative for resolved versions.

This is a navigation map, not a second architecture manual or a dated dependency snapshot.
