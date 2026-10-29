# Grounded extraction providers

`__init__.py` implements `RulesInference`, `OpenAIInference` and optional `AgentFrameworkInference`.

- Input is a persisted segment; output is typed extraction, provider identity and measured usage.
- Rules are an explicitly limited offline baseline, not an automatic model-failure fallback.
- Model output must pass schema/exact-evidence validation; retries and context limits are bounded.
- Optional debug payloads are separate from normal telemetry and never include authorization headers.

Configure capabilities explicitly; do not add autonomous tool loops.
See `tests\unit\test_inference.py`, `tests\golden\dataset.json` and `docs\plugins.md`.
