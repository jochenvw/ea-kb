# Persistence contract tests

`test_repositories.py` verifies explicit commits, grounding, idempotence, historical versions,
authority events, recursive lineage and ownership/lease semantics.
`test_resolution.py` covers evidence-backed immutable decisions, strict authority and stale-run fencing.

Tests use the UUID-schema fixture from `tests\conftest.py`; set `EKB_TEST_DATABASE_URL` first.
Run `uv run pytest -q tests\contract` from the repository root.
New persistence providers must retain these semantics rather than only matching method signatures.
