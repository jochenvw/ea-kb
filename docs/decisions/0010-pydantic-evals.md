# Pydantic Evals for skill evaluation

Use Pydantic Evals `Dataset.evaluate` to execute and report model-backed skill cases. Keep EKB's
typed task inputs, domain-specific scorers, quality gates, provenance checks and gold-data
isolation explicit; use pytest for software behavior and contract tests.

Bound concurrent cases and reserve a conservative model-call budget before admitting live cases.
Persist create-only detailed reports and compact indexes with dataset, prompt/model, code,
performance, usage and quality metadata so evaluation history is reproducible. Live sequential
versus parallel comparisons are an explicit opt-in, not part of offline verification.

This is an evaluation framework choice, not an inference-provider change. Model calls remain behind
the EKB inference port, with Microsoft Agent Framework preferred for configured skills. Pydantic
Evals does not replace Agent Framework or route inference through Pydantic AI. Version 2.60.0 is
the current lock resolution; its upstream dependency graph includes `pydantic-ai-slim`, which EKB
does not use as its inference backend.

The pilot reuses the labeling and extraction skill dataset and existing scorer semantics. Current
offline Rules results remain diagnostic, not model qualification; no live performance comparison
has been measured.
