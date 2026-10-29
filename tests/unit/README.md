# Fast offline regression tests

`test_*.py` covers domain properties, exact grounding, formats, sources, providers, configuration,
telemetry privacy, evaluation, corpus downloads and folder-guide coverage.

Use `uv run pytest -q tests\unit`; HTTP/model/Azure behavior is mocked or injected.
`test_folder_readmes.py` uses Git's tracked/new non-ignored inventory to catch missing guides,
including future plugin/extension directories; raw fixture-data subfolders are intentionally exempt.
Keep tests focused on introduced/fixed behavior rather than implementation details or live services.
