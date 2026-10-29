# Publication, semantic versions and scoped authority

## Decision

Keep five dimensions independent: original publisher version, normalized source SemVer,
publication maturity, concept SemVer and enterprise authority.

- A source edition such as `v2 (preview)` is preserved, not converted into `2.0.0-preview`.
- Normalize only explicit valid SemVer labels. Unknown source/concept versions remain null;
  byte revisions and exact occurrences still preserve their history.
- SemVer 2.0 prerelease precedence is supported, including numeric identifiers. Build metadata
  is retained but ignored for precedence. Explicit concept versions require a predecessor;
  multiple draft branches can share the same predecessor.
- Metadata and explicit status/title passages determine publication maturity. Each finding cites
  a persisted segment with exact offsets or an ingested metadata field. Missing evidence means
  unknown; ordinary references to approved software or preview features are not maturity claims.
- Immutable publication assessments are keyed by revision and detector/parser/metadata fingerprint.
  Original bytes and prior assessments are not rewritten when parsing improves.
- `review`, `approve`, `reject`, `promote` and `withdraw` are explicit, append-only authority events
  scoped to a concept version and applicability period. Approval never implies canonical adoption;
  publication maturity never generates an authority event.

## Consequences

The initial MVP's automatically generated `1.0.0` labels were not evidence-backed semantic
classifications. Migration `c9b12e70a641` changes those initial counters to unknown while preserving
version IDs, occurrences and explicit successor versions. It also removes the old 50-character
version limit. Downgrade requires an explicit data migration; silently recreating version claims
or deleting authority/publication history would be unsafe.

API, MCP, terminal and web inspection expose publication metadata and separate scoped authority
states. The absence of an authority event leaves a version proposed and not canonical.

## Evidence

`src\ekb\domain\publication.py`, domain models, PostgreSQL repositories,
`tests\unit\test_publication.py`, `tests\contract\test_repositories.py`,
`tests\integration\test_pipeline.py`, `tests\integration\test_interfaces.py`.
