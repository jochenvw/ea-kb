# Pydantic Evals for skill evaluation

Use Pydantic Evals `Dataset.evaluate` to execute and report new or materially changed AI/skill
evaluations. Keep EKB's typed task inputs, domain-specific scorers, quality gates and provenance
checks explicit; preserve gold-data isolation and use pytest for software behavior and contracts.

Bound concurrent cases and reserve a conservative model-call budget before admitting live cases.
Optional per-deployment quota limits reserve each provider attempt, including retries, separately.
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

Do not migrate the application to Foundry merely for parallel evaluation. Local execution with
uploaded results is distinct from cloud evaluation compute, which does not automatically run this
local parser/database/Python pipeline or bypass model quotas. Revisit managed jobs only after
checking deterministic custom evaluators, regional availability, permissions, approved data handling
and cost; avoid a classic-only API without reviewing the current SDK. A future Foundry integration
must complement, not replace, the local Pydantic Evals standard.
