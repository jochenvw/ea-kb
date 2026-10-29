# Durable reconciliation and knowledge resolution

The existing ingestion and benchmark services now run through replaceable `indexing -> enrichment
-> issues` stages. Candidates become authoritative only through accepted, scoped, evidenced
reconciliation. Objective and Requirement remain the only semantic primitives.

## Implementation and migration

| Files | Responsibility |
|---|---|
| `src\ekb\domain\execution.py`, `resolution.py`, `issues.py` | Typed executions, findings, decisions, aliases, resolver results and issues |
| `src\ekb\ports\workflow.py` | Stage/check and execution/resolution/issue repository contracts |
| `src\ekb\orchestration` | Explicit registration, dependency/contract validation, leases, partial execution and resume |
| `src\ekb\stages` | Existing-ingestion adapter, independent enrichment and issue checks; optional cached semantic providers |
| `src\ekb\services\knowledge_resolver.py`, `reconciliation_service.py`, `issue_service.py` | Shared strict resolution, immutable governance and review |
| `src\ekb\adapters\persistence\postgres\workflow.py`, `issues.py` | Durable state, atomic decisions/authority/aliases and issue lifecycle |
| `src\ekb\interfaces`, `runtime.py`, `config` | CLI/API/TUI/web/MCP integration and explicit composition |
| `src\ekb\observability`, `evaluation\workflow.py` | Correlated telemetry and isolated actual-corpus demonstration |
| `tests\unit\test_workflow_domain.py`, `tests\integration\test_workflow.py`, `tests\contract\test_resolution.py` | Focused domain, workflow, transport and shared persistence contracts |

Additive Alembic revision **`bc02fd5aa60d`** creates `pipeline_executions`, `pipeline_leases`,
`enrichment_findings`, `reconciliation_decisions`, `concept_aliases`, `knowledge_issues` and
`issue_events`. It reuses existing `authority_events`; no duplicate graph or ingestion schema.
Run `uv run ekb init` to migrate. Relevant folder guides document local entry points.

Execution records retain versions, fingerprints, configuration, attempts, timestamps, statistics
and output contracts. Source ingestion remains incremental. Check caches survive restart; a changed
check version re-evaluates that check. A 120-second global pipeline lease renews every 20 seconds,
fences writes and cancels work on heartbeat failure. Resume retains completed checkpoints and
marks abandoned child attempts cancelled. Transient retries are bounded.

## Plugin example and configuration

No plugin framework or queue is added. Register `PipelineStage` implementations with `StageRegistry`;
each declares a `ProcessorSpec` and returns a compatible `ArtifactReference`. To add a check:

```python
from ekb.domain.models import Model
from ekb.ports.workflow import CheckContext, CheckResult
from ekb.stages.enrichment import Check

class Parameters(Model):
    minimum: int = 2

async def inspect_assertions(context: CheckContext) -> CheckResult:
    options = Parameters.model_validate(context.options.parameters)
    # Inspect the supplied snapshot; return evidenced findings, never authority.
    return CheckResult(candidates=sum(
        len(c.occurrences) >= options.minimum for c in context.snapshot.concepts
    ))

orchestrator = runtime.orchestrator()
stage = orchestrator.registry.stages["enrichment"]
stage.checks = (*stage.checks, Check("custom", inspect_assertions, "1", Parameters))
await orchestrator.run(configuration=runtime.settings.pipeline)
```

Declare check dependencies in registered order. Unknown configuration, invalid typed parameters,
dependency cycles and incompatible upstream artifacts fail explicitly. Registering a model or
plugin does not activate network access. Select a YAML profile with `EKB_CONFIG_FILE`, for example:

```yaml
pipeline:
  retries: 1
  stages:
    enrichment:
      checks:
        semantic_duplicate:
          enabled: true
          similarity_threshold: 0.85
          max_candidates: 500
        exact_duplicate:
          auto_accept_exact: false
    issues:
      checks:
        excessive_deviations:
          threshold: 10
```

Exact recognition retains one existing identity and its separate occurrences. Semantic similarity,
version comparisons, published mappings, scope/authority and lineage checks persist proposals.
No semantic merge is automatic. Explicit `exact-identity-v1` policy alone can automatically accept
deterministic recognition with multiple occurrences of one evidenced version.

## Commands and interfaces

```powershell
uv run ekb pipeline run
uv run ekb pipeline run --stages indexing
uv run ekb pipeline run --stages enrichment
uv run ekb pipeline run --stages issues
uv run ekb pipeline status
uv run ekb pipeline resume RUN_UUID
uv run ekb reconcile list --check published_mapping --limit 20
uv run ekb reconcile history FINDING_UUID
uv run ekb reconcile decide FINDING_UUID accepted --actor operator --rationale "Reviewed evidence"
uv run ekb reconcile canonical CONCEPT_UUID VERSION_UUID --scope '{"organization":"Example","platform":"Azure"}'
uv run ekb issues list --severity high --scope '{"platform":"Azure"}'
uv run ekb issues show ISSUE_UUID
uv run ekb issues review ISSUE_UUID dismissed --actor operator --rationale "Reviewed false positive"
uv run ekb knowledge resolve CONCEPT_UUID
uv run ekb knowledge resolve CONCEPT_UUID --profile historical --as-of 2024-01-01
uv run ekb knowledge search "web application firewall" --profile best_available
```

Partial stages need matching, current successful upstream artifacts; they do not silently rerun
prerequisites. Decision updates require `--previous` with the latest decision ID. `--decision-id`
supports idempotent retries. Reverse an accepted decision explicitly before changing its outcome.

Web `/review` and the TUI Review tab call the same application services. API routes expose
`/api/knowledge/{id}/resolve`, `/api/knowledge/search`, `/api/reconciliation`, `/api/issues` and
`/api/pipeline/status`. HTTP mutations are disabled until `EKB_GOVERNANCE_TOKEN` is set; Basic
authentication uses the operator name and token password, requires HTTPS or loopback and rejects
cross-origin mutations. Local CLI/TUI actors are trusted operator assertions, not enterprise identity.
Non-health web/API routes reject non-loopback clients and forwarded-proxy headers. No enterprise
role-based or tenant authorization is implemented or claimed.

MCP remains read-only. `resolve_knowledge` and `resolve_requirement` default to strict canonical;
legacy search/get tools remain explicitly candidate research. Issue/history tools expose audit.

## Resolution and audit guarantees

Canonical resolution requires accepted reconciliation, applicable approved/canonical authority and
source evidence. Omitted consumer scope cannot satisfy a required organization/platform/application.
Retired versions and explicit superseded predecessors are excluded. Draft/preview adoption needs
explicit limited-scope approval; a newer version alone establishes nothing. Unsupported manually
changed statements cannot borrow unrelated source evidence.

`best_available` labels candidates and uncertainty without claiming authority. `historical` requires
an explicit **valid-time** date, not transaction-time reconstruction. `all` retains source/version/
decision history without implying authority. Accepted aliases preserve every source occurrence;
accepted mappings add related native provenance. Multiple applicable canonical versions return
explicit ambiguity, including competing versions within an accepted alias group.

Decision IDs are immutable idempotency keys; optimistic decision heads plus an advisory lock
serialize updates. Reversal appends audit rather than deleting evidence. Stable issue identities
preserve dismissal across unchanged reruns; material evidence reopens them. Disappearance records
`observed=false`, not human resolution. Accepted linked reconciliation can explicitly resolve an
ambiguity with decision references. Severity/scope filtering happens before shortlist limits.

## Actual-corpus demonstration

```powershell
uv run ekb eval workflow --output data\phase3-demonstration.json
```

Requires the retained Microsoft/NIST corpus from `sources sync` and indexing. This offline command
re-ingests **14 actual sources**: v1/v2 NS-6 articles and overviews, the native NIST catalog and
actual policy definitions. It adds four explicitly synthetic enterprise architecture facts and an
approved-exception detector input from `tests\fixtures\enterprise.json`.

All review actions are **simulated human decisions**, labeled `synthetic-demo-reviewer`, in persistent
schema **`ekb_phase3_demo`**. They never approve operational/public-schema knowledge. The evaluator
accepts one evidenced SC-7 mapping and one limited-scope preview adoption, reruns, constructs a new
resolver and verifies durable decisions, one authoritative sandbox result, full provenance and no
global canonical fallback. Repeated invocation retains the same decisions.

The final authoritative sandbox response contains **77 exact source occurrences** across two retained
original artifacts (the Microsoft article and native NIST catalog). Every excerpt was checked
against its stored segment coordinates and every referenced original was retrieved successfully.

The measured first demonstration found 19 exact-identity, 2 semantic-duplicate, 8 version and 239
published-mapping proposals, with 20 issues. **Five SC-7 references share one native anchor set**.
Both simulated decisions persist; unchanged enrichment checks reuse cache. One synthetic approved
exception remains below threshold. No paid model calls occur. Original Microsoft/NIST evidence is
kept distinct from synthetic architecture facts and simulated enterprise authority.

The complete actual-corpus run also completed after interruption/resume: 288 exact identities,
1 semantic duplicate, 68 version proposals and 1,885 published mappings; 161 grouped issues.
These are bounded rule-based observations, not exhaustive semantic quality or compliance scores.

## Executed verification

The PostgreSQL-backed full suite passed: **127 tests**. Ruff lint/format, basedpyright, Alembic
drift detection and lock consistency passed. Focused cases include real concurrent exclusion,
cancellation/resume, stale-owner fencing, provider/embedding cache replay, check/stage disabling,
alias-group ambiguity, scoped/draft/historical resolution, unsupported-version evidence rejection,
issue reopening/priority/owner preservation, authenticated HTTP and strict MCP output.

Actual full execution `209b50f6-ab0c-40af-9bb0-bc8bb86adcf6` and independent enrichment/issues
runs completed. Repeated demonstration run `ca293cad-eb1a-4667-91dc-fe41ad78309a` retained both
simulated decisions and skipped unchanged enrichment/issue checks. The public-schema default remains
`not_established`; no operational authority was fabricated.

All ten required successful OpenTelemetry span names and five local metric instruments were
verified. Machine-readable evidence remains in ignored `data\phase3-acceptance\verification.json`,
`spans.json`, `data\phase3-tests.txt`, full/partial run records and
`data\phase3-demonstration.json`. Reproduce the checks with:

```powershell
$env:EKB_TEST_DATABASE_URL = "postgresql+asyncpg://ekb:ekb@localhost:5432/ekb"
uv run pytest -q
uv run ruff check src tests migrations scripts
uv run ruff format --check src tests migrations scripts
uv run basedpyright
uv run alembic check
uv lock --check
uv run ekb eval workflow
```

## Telemetry and limits

Required pipeline/stage/check, candidate retrieval, semantic comparison, proposal/decision,
resolution and issue spans share run/finding correlation. Rotating local logs/traces/metrics retain
versions, input fingerprints, candidate/call/token/latency statistics, available model cost and
retry/error types without source text. Optional Azure exporters remain unverified live.

Candidate retrieval ranks token overlap, inspects at most 20 candidates per assertion and defaults
to at most 500 attempted comparisons; CPU-heavy comparisons run off the event loop. This is not
exhaustive semantic retrieval. Optional embeddings/verification reuse durable provider caches;
fake-provider tests demonstrate contracts, not model quality.

Snapshots fail explicitly beyond 20,000 rows per workspace table. Search is literal and bounded;
resolver search currently loads a snapshot per candidate. Historical parser interpretations remain
visible; current-candidate projection does not distinguish every parser generation of unchanged
bytes. Explicit canonical decisions prevent parser recency from becoming authority.

The default deviation repository returns no approved exceptions. A typed detector and synthetic
input demonstrate counting, not a production exception approval/storage workflow. No automatic
policy compliance, autonomous governance, enterprise authorization or live Azure deployment is claimed.
