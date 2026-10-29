# Domain values and evidence rules

- `models.py` defines frozen schemas, identity/fingerprint helpers, scope, versions and provenance.
- `grounding.py` checks exact evidence substrings and relationship candidate references.
- Objectives and requirements remain candidates until an explicit authority decision.

Keep this layer deterministic and independent of persistence, transport, cloud and model SDKs.
Do not infer missing scope, fabricate evidence or merge semantic similarity into identity.
See `tests\unit\test_domain.py`; strict typing applies to this package.
