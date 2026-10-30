# Deterministic document parsing

`StructuredParser` in `__init__.py` converts PDF, Office, OSCAL/JSON, XML, HTML and text into segments.
`csf.py` preserves native CSF Core IDs, parent links and per-page source text without semantic guesses.
Whole NIST CSWP 29 PDFs are detected by their cover text, or selected by `framework_namespace: nist-csf`.
Automatic detection preserves surrounding publication text outside the native Core pages.
Explicit page subsets retain generic PDF parsing; native hierarchy parsing requires the full Core.

- Preserve hierarchy and source locations; exact offsets refer to persisted parsed text.
- Blocking libraries run through `asyncio.to_thread`; OCR is selective and opt-in.
- Parser output/configuration changes must change the processing fingerprint.
- Images remain original-document references; do not invent diagram semantics or clause text.
- Document metadata is separate evidence, not body prose to send for semantic extraction.
- OSCAL control IDs and publisher label aliases survive titled parts; no enhancement IDs are guessed.

No model calls belong here. Start with `tests\unit\test_parsers.py` for format-specific examples
and `tests\integration\test_pipeline.py` for directory roundtrips.
