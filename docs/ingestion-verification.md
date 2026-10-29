# Public-corpus ingestion verification

Verified locally on 2026-10-29 against the main PostgreSQL database.
The available corpus completed **indexing -> enrichment -> issue detection** without ingestion
failures. A Windows telemetry rotation collision discovered during the run was fixed and verified.

## Browse the results

The local UI is running on <http://127.0.0.1:8000>.

| Page | Inspect |
|---|---|
| `/` | Workspace metrics, open exceptions and latest three-phase status |
| `/knowledge` | Search and cursor-page all concept candidates |
| `/sources` | Searchable source registry, retrieval status and publisher metadata |
| `/concepts/{uuid}` | Candidate versions, exact evidence, original source references and lineage |
| `/review` | Issue shortlist, reconciliation proposals and supporting passages |
| `/benchmarks` | Publisher controls, mappings, edition comparisons and evidence |
| `/pipeline` | Phase/check records, ingestion history and scan controls |
| `/docs` | Interactive API documentation |
| `/api/issues?limit=1000` | JSON inspection of up to 1,000 issues |
| `/api/pipeline/status` | Latest pipeline and individual stage/check executions |

This instance permits browsing but disables HTTP governance writes. No source was automatically
approved, merged or promoted to canonical. The shadcn review queue filters up to 1,000 issues;
historical proposals and the knowledge library have server-side cursor pages. Evidence and technical
identifiers are drill-downs, not the primary inventory. The terminal alternative is `uv run ekb tui`.

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

## Shadcn workspace verification

The subsequent browser release passed `uv run ekb test --ui`: **163 Python tests and 12 Playwright
regressions, zero skips**, plus offline evaluations. Browser regressions use synthetic API fixtures;
they cover evidence, scoped authority, governance guards, cursor paging, error recovery, structured
semantic versions, unknown processing cost and responsive themes.

Separate acceptance checks used the actual populated workspace: all **1,557 concepts** were reached
through eight cursor pages without duplicates; six desktop and six mobile pages loaded without
browser/API errors or horizontal overflow. Checks opened the duplicate issue's two interpretations,
supporting proposal, exact concept passages, known source version and read-only edition comparison.
The packaged wheel contains the exact final browser assets and third-party licenses.

Ignored evidence is under `data\ui-verification`: `suite\suite.json`, `browser-acceptance.json`,
final screenshots and `package\`. These browser checks do not imply live model qualification,
additional ingestion or approval of any proposal.

## Staged CLI and Foundry readiness

The subsequent CLI release passed **174 Python tests, zero skips**, plus offline evaluations.
Ruff lint/format, basedpyright, Alembic drift and lockfile checks passed. Disposable-state tests
covered parallel acquisition, the acquisition/extraction barrier, HTTP transfer versus cache reuse,
human/JSON reporting, failure exits, confirmed reset, active-work refusal and reset rollback.
The main corpus was not reset.

Actual CLI replay `12ace3ff-a594-4703-b7a0-5e21f804a6f9` completed all durable stages:
**235 found, 235 known, 0 unknown; 1 downloaded, 229 reused, 5 metadata-only; 0 processed,
235 skipped, 0 failures**. Enrichment and issue detection each reused six checks. Persisted
knowledge totals above remained unchanged. This replay used deterministic rules, not paid inference.
The live browser rendered the updated pipeline records, review queue and knowledge library.

Two deployments were added to an existing Sweden Central Foundry account: `ekb-student`
(`gpt-5.4-mini`, version `2026-03-17`) and `ekb-embedding` (`text-embedding-3-small`, version `1`).
Existing deployments were preserved. Entra-authenticated synthetic smoke calls returned one
exact-grounded candidate without validation failures, 1,536-dimensional embeddings and an
exact-duplicate semantic result. No corpus text was sent and no database records were written
by these smoke calls. **These models are not held-out qualified; pricing and cost remain unknown.**
Full infrastructure deployment is separate from adding these models to an existing account.

Ignored evidence is under `data\cli-verification`: `suite\suite.json`, `pipeline.json`,
`pipeline.txt` and `foundry-smoke.json`. The ignored `data\foundry-local.yaml` selects the deployed
models and the existing acceptance manifest:

```powershell
uv run ekb pipeline run --config data\foundry-local.yaml
# Explicit clean restart; prompts before deleting EKB knowledge and owned caches:
uv run ekb pipeline run --config data\foundry-local.yaml --fresh
```

These commands enable paid model calls. Environment overrides still take precedence; inspect the
printed model IDs. Use `--fresh --yes` only for intentional unattended resets. The portable
`config\foundry.yaml` template and reset boundaries are documented in `usage.md`.

## Reference, work and operational inspection

The reference/work release passed the combined `uv run ekb test --ui` suite:
**180 Python tests, 17 Playwright regressions, zero skips**, plus offline evaluations.
Ruff lint/format, basedpyright and Alembic drift checks passed. New cases cover evidence-bound
source versions, graph direction/proposal distinction, decision-head backlog filtering,
obsolete-run exclusion, manual proposals, table allowlisting/cursors and bounded private log tails.

Separate acceptance checks used the actual populated workspace: **238 sources**, a source
drill-down with **29 evidenced versions/passages**, a **5-node/4-edge** duplicate neighborhood,
**2,269 outstanding proposals**, all **24 EKB tables**, and **100 recent log events**.
Source-to-version-to-evidence navigation, graph recentering and foreign-key record links worked.
Six reference/work/operations pages were checked at desktop and mobile sizes without browser/API
errors or horizontal page overflow. The instance stayed read-only; no mutations, model calls,
new ingestion or governance decisions were performed by these checks.

The sidebar now separates Work (backlog and pipeline), Reference (sources, knowledge, visual
explorer, benchmarks and reconciliation ledger), and Operations (data and log inspection).
The reference ledger retains historical proposals; the work backlog excludes accepted/rejected
heads and proposals outside the latest successful enrichment output, except manual proposals.
Ignored evidence and screenshots are under `data\reference-work-verification`, including
`suite\suite.json` and `browser-acceptance.json`.
