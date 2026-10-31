# Domain values and evidence rules

- `models.py` defines frozen schemas, identity/fingerprint helpers, scope, versions and provenance.
- Strategic-goal level and request/retry usage are evaluation metadata, not OWL vocabulary.
  Retry counts are optional measurements, not inferred from multiple model calls.
- `publication.py` assesses evidenced maturity and projects time/scoped authority independently.
- `grounding.py` checks exact evidence substrings and relationship candidate references.
- `skills.py` validates labeled scope evidence and extraction routing/qualifiers without model calls.
  Ontology types are distinct from organizational/platform scope; unknown layers remain nullable.
- Optional evidence-backed content coverage describes company/unit breadth, not adoption or OWL types.
- Scoped extraction rejects both omitted and invented numeric qualifiers, including technical versions.
- `reconciliation.py` types generic assertions, judgments and discriminated replay entries.
  Cached vectors are finite, nonzero and bounded; judgments require source-grounding checks on use.
  Verification status is separate from adapter-defined methods and from an `unknown` judgment.
- `execution.py`, `resolution.py`, `issues.py` define durable runs, audit and issue lifecycle;
  alias edges derive from findings and effective decision heads.
- `inspection.py` types source-bound extraction pages, graphs, backlog and read-only diagnostics.
- Knowledge levels separate company intent, platform-agnostic architecture, platform-specific
  controls and unclassified interpretations; they grant no authority.
- `ontology.py` types the read-only authoritative catalog; `SemanticType` and `RelationshipKind`
  use OWL identifiers, not a parallel objective/requirement taxonomy or lifecycle relation kinds.
- Strategic goals and requirements remain candidates until an explicit authority decision.
- `assertions.py` guards concise phrasing, qualifiers and source-bound replay fingerprints.

Keep this layer deterministic and independent of persistence, transport, cloud and model SDKs.
Do not infer missing scope, fabricate evidence or merge semantic similarity into identity.
See `tests\unit\test_domain.py`; strict typing applies to this package.
