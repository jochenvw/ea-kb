# 0007: Bounded ingestion skills and precision-gated model selection

Accepted 2026-10-30.

Keep inventory, retrieval, hashing, parsing and native structured materialization deterministic.
Finish file inventory/acquisition before invoking semantic skills. Unknown local formats remain
visible as metadata-only inventory, without pretending that their content was processed.

Use Microsoft Agent Framework behind the inference port for model-backed profiles. Distinct
semantic tasks are separate bounded skills: ontology/scope labeling, then extraction within those
labels. Each owns a prompt, typed output, fingerprint and independently runnable evaluation.
Do not introduce a dynamic multi-agent planner or autonomous tools for this fixed flow.

Choose the least expensive model that passes precision, grounding, scope, recall and efficiency
gates. A separate labeler is optional; missing pricing or offline success cannot qualify it.
Do not trade precision for cheapness, infer authority, broaden scope or silently downgrade models.

Reuse existing JSON replay contracts: commit labels before extraction and retain atomic extraction
persistence. Independent evaluation uses gold labels to isolate the extractor; end-to-end evaluation
is also required. Current labels/cases are provisional, not production quality evidence.
See `docs\architecture.md`, `docs\plugins.md` and `docs\evaluation.md`.
