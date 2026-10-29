# Public-corpus ingestion verification

Verified locally on 2026-10-29 against the main PostgreSQL database.
The available corpus completed **indexing -> enrichment -> issue detection** without ingestion
failures. A Windows telemetry rotation collision discovered during the run was fixed and verified.

## Browse the results

The local UI is running on <http://127.0.0.1:8000>.

| Page | Inspect |
|---|---|
| `/` | Search objective/requirement candidates; sources, scans and processing tasks |
| `/concepts/{uuid}` | Candidate versions, exact evidence, original source references and lineage |
| `/review` | Issue shortlist, reconciliation proposals and supporting passages |
| `/benchmarks` | Publisher controls, mappings, edition comparisons and evidence |
| `/docs` | Interactive API documentation |
| `/api/issues?limit=1000` | All 161 current issues, beyond the UI shortlist |
| `/api/pipeline/status` | Latest pipeline and individual stage/check executions |

This instance permits browsing but disables HTTP governance writes. No source was automatically
approved, merged or promoted to canonical. The review page is a bounded shortlist, not a complete
export of historical proposals. The terminal alternative is `uv run ekb tui`.

## Checks and downloads

- **159 code tests passed, zero skips**, plus offline extraction/reconciliation/retrieval safety evals.
- Ruff lint/format, basedpyright, Alembic drift and lockfile checks passed.
- All **221 benchmark assets** were revalidated/reused; no changed originals required redownloading.
- **Six supplemental files** were freshly downloaded, including PDF, DOCX and OSCAL JSON.
- EUR-Lex returned HTTP 202/access-challenge content, including its official PDF alternative;
  that regulation was explicitly excluded, not treated as a successful document.
- ISO metadata retrieval returned HTTP 403. The two ISO source entries remain metadata only;
  no licensed normative full text was downloaded or invented.

The combined manifest contains 17 source specifications, including benchmark seeds that expand
into cached assets. Existing configured page/control selections remain explicit: the corporate
annual-report sample uses pages 8-15; native NIST catalogs use selected controls. Original files
are retained in full, but these selections are **not full-document/full-catalog extraction**.
No live model or embedding calls were enabled; these results do not qualify a cheap AI model.

## Actual run and persisted results

Initial pipeline: `abff2d57-c392-4cb3-85ea-3edb823fd36c`.
Replay after the telemetry fix: `5bb6bcdf-e785-4e28-9a25-63b7d537b8ae`.

| Measure | Result |
|---|---|
| Initial scan | 235 discovered, 11 processed, 224 skipped, 0 failed |
| Incremental replay | 235 discovered, 0 processed, 235 skipped, 0 failed |
| Pipeline stages | All three completed; unchanged enrichment/issue checks replayed from cache |
| Stored concepts / versions | 1,557 / 1,793 |
| Stored segments / exact evidence occurrences | 6,559 / 2,407 |
| Verified original-document hashes | 234, no mismatches |
| Verified occurrence offsets/excerpts | 2,407, no mismatches |
| Active proposals / all retained proposals | 2,269 / 2,557 |
| Currently observed open issues | 160 unresolved published mappings; 1 possible duplicate |
| Reconciliation decisions / authority events | 0 / 0 |

Database totals include historical records and earlier fixture records, not just newly discovered
inputs. Proposals and issues remain review candidates, not proven violations. Unresolved publisher
mappings mean a native target is not established, not that the source quotation is corrupt.

The initial concurrent UI/CLI run exposed Windows file-sharing errors when two processes rotated
the same telemetry file. Writers now use `data\telemetry\<process-id>\`. A two-process regression
forces rotation; the actual pipeline replay also forced 2 KiB rotation while the UI was running
and completed with empty stderr. All listed UI/API pages returned HTTP 200 after verification.

## Local evidence and replay

Ignored runtime evidence is in `data\acceptance-latest`: `suite\suite.json`, `benchmark-sync.json`,
`download-coverage.json`, `pipeline-run.json`, `pipeline-replay.json`, and `verification.json`.
The generated combined manifest and pipeline profile are preserved there as well; default
version-controlled profiles were not replaced.

From this working copy:

```powershell
$env:EKB_CONFIG_FILE = (Resolve-Path data\acceptance-latest\pipeline.yaml).Path
uv run ekb pipeline run
uv run ekb web
```

The web server is already running; the last command is for restarting it after stopping that instance.
For raw database inspection, use the local PostgreSQL `ekb` database, normally port 5432.
Read `sources`, `source_revisions`, `segments`, `concept_versions`, `occurrences`,
`enrichment_findings`, `knowledge_issues` and `pipeline_executions`.
Use application services for mutations rather than editing provenance or governance rows directly.
