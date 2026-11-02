# Synthetic ingestion inputs

`local` contains the small OSCAL catalog, governance text and ISO metadata-only fixture.
These are deterministic test inputs, not external publications or approved enterprise policy.
`enterprise.json` explicitly labels four synthetic architecture facts and an approved-exception input;
the demonstration never treats these as Microsoft/NIST obligations or real enterprise approval.

`reference-app` is a synthetic end-to-end assessment example: `requirements.json` (obligation and
baseline), `design\*.json` (structured design), `infra\main.bicep` with its compiled `main.json`,
`parameters\*` profiles, `cases.json` variations and `expected\findings.json`. `site` is a placeholder
page. Run it with `uv run pytest -q tests\integration\test_reference_app.py` (requires
`EKB_TEST_DATABASE_URL`); `test_portfolio_service.py` reuses it for exception risk and change impact.

Do not place navigation READMEs inside raw fixture-data subfolders: directory scans would ingest them
and change source inventories. Keep fixture descriptions here instead.
Format-specific PDF/Office cases are generated in temporary directories by parser/integration tests.
See `tests\integration\test_pipeline.py`; never add licensed ISO full text.
