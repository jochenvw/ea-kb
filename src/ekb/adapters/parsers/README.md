# Deterministic document parsing

`StructuredParser` in `__init__.py` converts PDF, Office, OSCAL/JSON, XML, HTML and text into segments.

- Preserve hierarchy and source locations; exact offsets refer to persisted parsed text.
- Blocking libraries run through `asyncio.to_thread`; OCR is selective and opt-in.
- Parser output/configuration changes must change the processing fingerprint.
- Images remain original-document references; do not invent diagram semantics or clause text.

No model calls belong here. Start with `tests\unit\test_parsers.py` for format-specific examples
and `tests\integration\test_pipeline.py` for directory roundtrips.
