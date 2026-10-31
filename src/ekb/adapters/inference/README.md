# Grounded extraction providers

`__init__.py` implements `RulesInference`, `OpenAIInference` and optional `AgentFrameworkInference`.
`reconciliation.py` adds explicitly configured small-model verification and bounded embeddings.
`assertions.py` rephrases selected candidates as source-grounded single-sentence assertions.
`skills\labeling.md`, `skills\extraction.md` and `skills\strategy_reconciliation.md` are independently
hashed, bounded runtime prompts.
Canonical OWL definitions are appended by task; definition/schema changes invalidate prompt replay.

- Input is a persisted segment; output is typed extraction, provider identity and measured usage.
- `label` identifies ontology spans/scopes; `extract_scoped` cannot broaden their scope or type.
  A separate labeling deployment is optional; unknown layers remain explicit.
- `reconcile_strategy` reconciles goals and strategic requirements, corrects overclassified
  requirements, and removes duplicates without dropping distinct scope or qualifiers;
  every goal level is allowed, while deterministic validation rejects unsupported ontology types,
  invented evidence, relationships and missing source keys.
- Rules are an explicitly limited offline baseline, not an automatic model-failure fallback; they
  emit only explicit organization-vision goals rather than promoting generic aim/target wording.
- Model output must pass schema/exact-evidence validation; retries and context limits are bounded.
- Successful skill results measure started transport/validation retries and throttles, and preserve
  provider model IDs and cached-input tokens when available. Missing usage keeps cost unknown.
- Agent Framework wraps SDK transport errors; the adapter maps their underlying status/connection
  errors to the same bounded retry policy as direct HTTP without retrying non-transient failures.
- Each configured adapter exposes its maximum requests per skill, including transient and validation
  attempts. Injected SDK clients and HTTP transports must disable their own retries. A scoped request
  limiter reserves each transport attempt for the actual deployment; use a dedicated evaluation instance.
- Optional debug payloads are separate from normal telemetry and never include authorization headers.
- Reconciliation validates evidence from both inputs; invalid model answers remain unknown.
- Embedding and verification costs remain unknown if prices or request usage are unavailable,
  including unmetered retries; cached results never repeat their original usage.
- Embedding usage is complete only if every request has positive metering, independently of pricing;
  an empty request has complete zero usage, while unmetered retries make usage incomplete.
- Neither an embedding match nor a model answer merges concepts or promotes authority.
- Assertion rewrites retain modality, numeric qualifiers and negation; unsupported phrasing stays unresolved.
- Chat payloads support explicit completion-token fields and omitted temperature; config affects
  inference/verifier replay identity. Configure per-token prices for measured qualification cost.

Configure capabilities explicitly; do not add autonomous tool loops.
See `tests\unit\test_inference.py`, `tests\golden\dataset.json` and `docs\plugins.md`.
