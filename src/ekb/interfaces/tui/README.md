# Interactive terminal interface

`KnowledgeApp` in `__init__.py` uses Textual for source, concept, evidence and review inspection.

- Runtime/services supply knowledge and explicitly governed review.
- Ingestion execution and monitoring belong to CLI commands and the worker.
- Preserve widget IDs used by the headless interaction tests.
- Retrieval and publication statuses are separate columns; selected evidence includes scoped authority.
- The Benchmarks tab inspects qualified control lineage or whole-edition comparisons read-only.
- Review calls shared reconciliation/issue services; resolver and rationale/actor fields retain audit.

Launch with `uv run ekb tui` from the repository root.
See `tests\integration\test_interfaces.py`; do not duplicate extraction or repository logic here.
