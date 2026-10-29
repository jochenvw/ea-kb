# 0001: Evidence and restartable transactions

**Decision.** PostgreSQL is authoritative. Ports return immutable Pydantic values; only the
PostgreSQL adapter imports SQLAlchemy. Services receive an explicit unit-of-work factory.
Transactions are short and explicit. Exiting a unit of work rolls back anything not committed.

Source identity derives from a canonical local URI or a manifest identifier. Source revision
identity derives from source identity and SHA-256 content. Processing identity additionally
includes parser, schema, model, prompt and extraction configuration. Returning to old content
reuses its historical revision; a document revision never automatically increments a semantic version.

Parsing has a durable checkpoint, including empty results. Each segment's extraction, evidence,
relationships and replay checkpoint commit together. Exact candidate duplicates share a concept
identity only when statement, kind, modality and scope match. Similarity is retrieval, not merging.
Explicit semantic revisions require an operator-supplied version and predecessor; no automatic
major/minor/patch classifier or promotion is implemented.

Processing tasks use renewable leases and atomic conditional claims. Every persistence transaction
checks ownership. A crashed worker's task becomes reclaimable after five minutes. Model completion
before a checkpoint commit can repeat a call after a crash: processing is at-least-once, committed
knowledge is idempotent. No claim of exactly-once billing is made.

**Consequence.** Historical occurrences remain resolvable even if a document changes, disappears,
or becomes inaccessible. Every persisted assertion has a real segment and exact substring offsets.
Authority events are explicit append-only records, separate from extracted textual modality.
