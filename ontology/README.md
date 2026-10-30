# Application ontology

Store the application-wide formal ontology here as version-controlled OWL/RDF source files.
Keep this separate from `sources` (ingestion manifests) and `data` (ignored runtime artifacts).

- Preserve supplied ontology files, namespaces, ontology identifiers, versions and attribution.
- Retain the supplied serialization, such as Turtle (`.ttl`) or RDF/XML (`.owl`, `.rdf`).
- Keep required local imports alongside the ontology and preserve their relative paths.
- Do not place credentials, downloaded corpus documents or generated runtime data here.

This folder currently reserves the storage location; no ontology has been supplied or wired into
the application. Adding files does not enable loading, validation, reasoning or ingestion.
Runtime integration belongs in the existing domain/port/adapter boundaries; see
[`docs/architecture.md`](../docs/architecture.md).
