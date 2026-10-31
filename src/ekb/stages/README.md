# Replaceable processing stages and checks

`indexing.py` adapts the existing ingestion engine without merging or granting authority.
`enrichment.py` registers deterministic and optional semantic checks; `issues.py` registers detectors.
`__init__.py` runs independently cached checks and persists their typed outputs, not their algorithms.
Check-result commits renew the existing pipeline owner and reject expired or replaced ownership.
`semantic.py` unions lexical candidates with bounded-pool embedding neighbors when configured;
zero lexical overlap does not prevent semantic discovery. Provider/input-keyed vectors and grounded
judgments replay across changed check versions; changed providers or semantic inputs miss the cache.
`provenance.py` runs ordered company alignment, control-parent mapping and sibling comparison;
later checks consume earlier findings from the same stage without treating them as approved policy.
Cache hits incur no calls, tokens or cost. Unpriced or unmetered calls keep total cost unknown.
Provider calls run outside transactions. Verifier results stay review-only.
Inject stage/check implementations through `StageRegistry`; each declares versions and contracts.
Defaults never enable paid inference or automatic semantic merges. See `docs\phase3.md`.
