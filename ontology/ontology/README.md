# Vocabulary and validation shapes

- `ekb.ttl` contains the OWL/RDF vocabulary, version `0.1.0-draft.3`.
- `ekb.shacl.ttl` contains the accompanying SHACL constraints for instance data.
- Preserve ontology identifiers, namespaces and version metadata when importing revisions.
- Runtime loads `ekb.ttl` for canonical definitions and vocabulary checks.
- SHACL shapes are optional instance validation, not automatic ingestion enforcement or reasoning.
- See the [bundle guide](../README.md) for semantics and optional validation commands.
