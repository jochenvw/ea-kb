# Synthetic ingestion inputs

`local` contains the small OSCAL catalog, governance text and ISO metadata-only fixture.
These are deterministic test inputs, not external publications or approved enterprise policy.

Do not place navigation READMEs inside raw fixture-data subfolders: directory scans would ingest them
and change source inventories. Keep fixture descriptions here instead.
Format-specific PDF/Office cases are generated in temporary directories by parser/integration tests.
See `tests\integration\test_pipeline.py`; never add licensed ISO full text.
