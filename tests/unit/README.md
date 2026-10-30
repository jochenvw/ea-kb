# Fast offline regression tests

`test_*.py` covers domain properties, exact grounding, formats, sources, providers, configuration,
telemetry privacy, evaluation, corpus downloads and folder-guide coverage.
`test_domain.py` also guards domain/port/application imports against infrastructure dependencies.
`test_reconciliation.py` covers generic grounded judgments, unknown scope and provider contracts.
`test_workflow_domain.py` covers plugin configuration, scope/conflict rules, bounded comparisons
and derived alias history.
It also verifies semantic replay keys, corruption recovery, call accounting and transaction boundaries.
`test_ingestion_skills.py` covers labeling/routing, scope/qualifier gates, SDK transport and replay.
It also checks Pydantic Evals concurrency/budgets, immutable history, mixed labels and source evidence.
`test_skill_evaluation_history.py` covers scorer parity, bounded submission, quota accounting,
multi-call/retry measurements, provider identity/cache evidence, partial usage, cancellation,
atomic history and incomparable run conditions using offline transports.
`test_nist_evaluation.py` covers supplied references, native PDF hierarchy, evidence, replay and unrun negatives.
`test_ontology.py` checks exact OWL definitions, domain/SQL/browser parity and read-only access.
`test_frugal_evaluation.py` covers split leakage, train-only exports, quality/cost gates and suite failure.
`test_infra.py` compiles/lints Bicep and checks ARM safeguards offline when Azure tooling is available.

Use `uv run pytest -q tests\unit`; HTTP/model/Azure behavior is mocked or injected.
`test_folder_readmes.py` uses Git's tracked/new non-ignored inventory to catch missing guides,
including future plugin/extension directories; raw fixture-data subfolders are intentionally exempt.
Keep tests focused on introduced/fixed behavior rather than implementation details or live services.
