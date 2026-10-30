# Ingestion verification

The offline contracts exercise ordinary HTTP, OSCAL and local-file connectors through the shared
ontology pipeline. They use mocked publisher responses or synthetic fixtures, never live downloads
or paid model calls. Real-source excerpts in evaluation datasets retain their exact publisher,
URL, license and recorded full-original hash; an excerpt or a mock response is not the original.

## What is checked

- Source identity, content-addressed originals, conditional requests, revision/checkpoint reuse
  and idempotent replay. Unchanged inputs do not repeat parsing or inference.
- Exact evidence coordinates and independent provenance for source occurrences. The classification
  contracts cover conceptual, technical and explicitly platform-scoped requirements; an unknown
  layer remains null. Explicit product/platform qualifiers survive without inferring them from a
  publisher or document title.
- Pydantic Evals executes provisional, attributed Microsoft guidance and NIST framework cases
  alongside synthetic negatives. Labels are not independent human gold and an offline rules
  baseline is not a qualified model. A documented NIST case retains a known reference disagreement.
- Generic embedding and semantic-verification replay survives check-version changes, invalidates on
  provider or input changes, rejects invalid/ungrounded cache entries and does not bill cache hits.
- New schemas omit the four removed historical benchmark tables and `concept_aliases`. Guarded
  reset/recreation removes all five legacy tables only in the active schema, refuses active work,
  does not cascade, and rolls back if another table depends on one. Source inputs, filesystem/Azure
  originals and unrelated tables are retained.
- Source publication maturity, version, authority and scoped canonical adoption remain separate.
  Published guidance and unresolved mappings never establish enterprise approval or compliance.

## Run focused contracts

```powershell
uv run pytest -q tests\unit\test_ingestion_skills.py tests\unit\test_publication.py
uv run ekb eval skills --output data\skill-evaluation.json

# Local disposable-schema PostgreSQL tests; replace with your disposable server if needed.
$env:EKB_TEST_DATABASE_URL = "postgresql+asyncpg://ekb:ekb@localhost:5432/ekb"
uv run pytest -q tests\integration\test_pipeline.py tests\integration\test_ontology_schema.py `
  tests\integration\test_scan_cli.py tests\integration\test_workflow.py
```

Database skips are not evidence of database correctness. Optional provider-adapter tests may skip
when their extras are not installed; `ekb eval verify-corpus` checks retained content-addressed
bytes and teacher quotations when all expected originals are available.

## Inspect results

Use `uv run ekb pipeline status`, `uv run ekb scans list`, `uv run ekb scans tasks` and
`uv run ekb scans usage` for persisted ingestion status and diagnostics. The optional browser and
TUI inspect sources, evidence and governance; neither they nor HTTP APIs start or monitor
ingestion. Review actions remain governed separately.

## Earlier local evidence

Before the specialized ingestion path was removed, local run
`abff2d57-c392-4cb3-85ea-3edb823fd36c` and replay `5bb6bcdf-e785-4e28-9a25-63b7d537b8ae`
reported 235 discovered sources, all skipped on replay, and no mismatches among 234 original hashes
or 2,407 evidence offsets. These historical figures do not verify the current schema or qualify a
model. Related ignored artifacts under `data\acceptance-latest`, `data\ui-verification`,
`data\cli-verification` and `data\reference-work-verification` may not be present in a checkout.

See [evaluation.md](evaluation.md) for dataset splits, provisional labels and live qualification
gates, [usage.md](usage.md) for disposable PostgreSQL setup and source manifests, and
[architecture.md](architecture.md) for pipeline and evidence boundaries.
