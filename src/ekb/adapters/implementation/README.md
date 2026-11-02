# Implementation analyzers

Deterministic adapters behind the `ImplementationAnalyzer` port (`ekb.ports`). They turn supplied
implementation artefacts into versioned `ImplementationObservation`s for the assessment reducer.

- `arm.py`: evaluates a small, explicit subset of Azure Resource Manager (ARM) template
  expressions with parameter files, conditions, copy loops and inline (Bicep module) deployments.
  Runtime functions, linked templates, `existing` resources and absent properties stay unknown.
- Checks are explicit functions keyed by check ID (`CHECKS`); an unknown check or resource type
  returns `unsupported`. Each resource instance is observed separately.
- `compile_bicep` only calls the locally installed Bicep compiler; templates are never executed.

Tests: `uv run pytest -q tests\unit\test_implementation_analyzer.py`. Contract and assessment
semantics: `docs\architecture.md` and `src\ekb\domain\README.md`.
