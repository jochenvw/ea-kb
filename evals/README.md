# Frugal model qualification

`teacher-v2.json` contains 18 source-grounded annotations across ten retained public documents.
Fifteen GPT-6.1 Sol labels are carried forward; three supplemental annotations identify Copilot Auto
with model identity unavailable. Source hashes/attributions distinguish exact quotations from
generated examples; all labels remain teacher-only, not independent human gold or enterprise
authority. Source families stay in one split. `gates.json` defines provisional student/retrieval
quality and efficiency requirements.
`skills-v1.json` isolates labeling and scoped extraction: three attributed excerpts plus 13 neutral
synthetic cases, with family/split isolation and provisional ontology labels.
`goals` contains provisional annual-report goal/target/non-goal annotations with exact page evidence.
`documents-v1.json` reuses those skill contracts for selected Shell annual-report and NIST CSF
passages plus an explicitly metadata-only ISO negative. It separates company-level coverage,
mixed ontology classes and named organizational scope; no standard is automatically adopted.
`provenance-v2.json` qualifies grounded control-parent selection with validation/test-isolated child
document families, ten principles, the Network Security Perimeter chain and an unrelated-parent
negative; expected IDs never enter model prompts. `provenance-v1.json` is retained unchanged as a
legacy split whose results are not comparable to the isolated v2 qualification.
`provenance-complete-v1.json` covers all 385 controls from the reviewed corpus, including reviewed
no-parent outcomes and independently generated retrieval candidates. Like v1, its legacy splits
are component diagnostics, not document-family-isolated qualification.
`architecture-assessment-v2.json` qualifies verified solution assessment (policy -> design -> ARM
template) across encryption, logging, network, identity, retention and secrets families: omissions,
alternatives, exceptions (valid/expired/future/scope/version/renewed), unsupported and external
evidence, multi-instance resources, missing authority and basis invalidation. It is synthetic,
deterministic and offline; it does not verify deployed state. Reports gate on zero false passes.
The provenance v2 cases also cover production retrieval, strict no-parent, negation, conditional scope,
misleading shared mechanisms and carry-forward; component and production reports remain separate.
Original PDF hashes/pages can be audited with `uv run ekb eval documents --verify-sources`.
`nist-csf` imports the supplied Core hierarchy, 25 provisional mappings and 14 negative challenges,
with separately source-audited excerpts, locator corrections and explicit reference disagreements.
Run `uv run ekb test` for code tests plus offline evals; `--live` explicitly enables configured models.
See `docs\evaluation.md` for measured results, source verification and training-only export.
