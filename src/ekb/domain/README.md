# Domain values and evidence rules

- `models.py` defines frozen schemas, identity/fingerprint helpers, scope, versions and provenance.
- `publication.py` assesses evidenced maturity and projects time/scoped authority independently.
- `grounding.py` checks exact evidence substrings and relationship candidate references.
- `benchmarks.py` defines source/control/mapping/policy records, not additional semantic primitives.
- `reconciliation.py` types comparisons, grounded judgments and explicit structural-only proposals.
- `execution.py`, `resolution.py`, `issues.py` define durable runs, audit, aliases and issue lifecycle.
- `inspection.py` types source-bound extraction pages, graphs, backlog and read-only diagnostics.
- Knowledge levels separate goals/guidance from explicit platform requirements; they grant no authority.
- Objectives and requirements remain candidates until an explicit authority decision.
- `assertions.py` guards concise phrasing, qualifiers and source-bound replay fingerprints.

Keep this layer deterministic and independent of persistence, transport, cloud and model SDKs.
Do not infer missing scope, fabricate evidence or merge semantic similarity into identity.
See `tests\unit\test_domain.py`; strict typing applies to this package.
