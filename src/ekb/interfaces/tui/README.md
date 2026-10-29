# Interactive terminal interface

`KnowledgeApp` in `__init__.py` uses Textual for inventory, progress, concepts, evidence and usage.

- Runtime/services supply knowledge; the existing ingestion pipeline performs scans.
- Keep input/rendering responsive while asynchronous work runs.
- Preserve widget IDs used by the headless interaction tests.
- Retrieval and publication statuses are separate columns; selected evidence includes scoped authority.

Launch with `uv run ekb tui --directory tests\fixtures\local` from the repository root.
See `tests\integration\test_interfaces.py`; do not duplicate extraction or repository logic here.
