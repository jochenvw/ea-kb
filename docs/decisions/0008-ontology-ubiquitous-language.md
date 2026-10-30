# 0008: Authoritative ontology and shared application vocabulary

Status: accepted.

The committed OWL/RDF vocabulary is the ubiquitous language of the application, not an optional
documentation glossary. Database semantic fields, domain schemas, prompts and browser labels must
use its class/property identifiers and definitions.

- Replace generic objective/requirement kinds with nullable ontology `semantic_type`.
- Constrain normalized concept classes and semantic relation names in PostgreSQL.
- Keep provenance, version succession, comparison outcomes and governance outside semantic relations.
- Preserve uncertainty: source evidence that does not establish a class remains unclassified.
- Read canonical Turtle with RDFLib; package the source, not a manually maintained definition copy.
- Expose a read-only catalog and browser with definitions, IRIs, version and OWL characteristics.
- Check OWL/domain/database/browser agreement and exercise real PostgreSQL constraints.

The user explicitly authorized clearing indexed data and previous runs; no compatibility or inferred
historical mappings are required. `ekb init --recreate` rebuilds owned tables/caches without
re-indexing under the pre-v1 policy in decision `0009`; no compatibility migration is required.
Source inputs, configuration, migration state, reports/logs and unrelated tables remain separate.

Scope labels remain evidenced facets rather than a normalized organizational entity graph.
The catalog does not perform reasoning, automatically inherit applicability, establish enterprise
authority or make classification context into requirement candidates.
