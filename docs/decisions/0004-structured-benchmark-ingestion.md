# Structured benchmark ingestion

Status: accepted.

Microsoft publishes structured control IDs, principles, mappings, workbooks and policy definitions.
Extract these deterministically before considering AI. Explicit sync owns network access and named
artifact checkpoints; normal discovery/scan consumes validated cache without network or model calls.

Each immutable benchmark snapshot binds a raw revision, an exact normalized-text segment, parser
version and extractor version. Typed JSONB records retain all controls, published references,
retrieval metadata and semantic concept bindings. These records are not new semantic primitives:
only objectives and requirements enter the knowledge model. Oversized statements stay complete in
the record, explicitly unmaterialized, instead of losing qualifiers through truncation.

Source publication maturity remains source-specific. The v1 workbook's private-preview statement
does not determine the v1 web article's maturity. An overview's preview edition is distinct from an
article with no explicit status. Neither publisher criticality, edition labels nor policy-definition
versions assign a concept SemVer or grant enterprise authority. Unknown targets remain references;
exact framework namespace/revision resolution is separate from semantic reconciliation.

The existing claim/heartbeat fence covers atomic segment, maturity, concept and snapshot persistence.
Model configuration changes do not invalidate structured work. Parser/extractor changes preserve
earlier snapshots and exact occurrences, while existing concept identity stays unchanged.
