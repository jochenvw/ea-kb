# Grounded extraction providers

`__init__.py` implements `RulesInference`, `OpenAIInference` and optional `AgentFrameworkInference`.
`reconciliation.py` adds explicitly configured small-model verification and bounded embeddings.
`assertions.py` rephrases selected candidates as source-grounded single-sentence assertions.
`skills\labeling.md` and `skills\extraction.md` are independently hashed, bounded runtime prompts.
Canonical OWL definitions are appended by task; definition/schema changes invalidate prompt replay.

- Input is a persisted segment; output is typed extraction, provider identity and measured usage.
- `label` identifies ontology spans/scopes; `extract_scoped` cannot broaden their scope or type.
  A separate labeling deployment is optional; unknown layers remain explicit.
- Rules are an explicitly limited offline baseline, not an automatic model-failure fallback.
- Model output must pass schema/exact-evidence validation; retries and context limits are bounded.
- Usage counts actual completion requests, including transient retries, for evaluation accounting.
- Optional debug payloads are separate from normal telemetry and never include authorization headers.
- Reconciliation validates evidence from both inputs; invalid model answers remain unknown.
- Neither an embedding match nor a model answer merges concepts or promotes authority.
- Assertion rewrites retain modality, numeric qualifiers and negation; unsupported phrasing stays unresolved.
- Chat payloads support explicit completion-token fields and omitted temperature; config affects
  inference/verifier replay identity. Configure per-token prices for measured qualification cost.

Configure capabilities explicitly; do not add autonomous tool loops.
See `tests\unit\test_inference.py`, `tests\golden\dataset.json` and `docs\plugins.md`.
