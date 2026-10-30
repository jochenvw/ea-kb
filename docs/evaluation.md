# Source-grounded, frugal evaluations

EKB separates **offline software/safety verification** from **live model qualification**.
One passing offline command does not establish that a cheap model or embedding deployment works.
No live model or Azure deployment is claimed by this implementation.

## Dataset and teacher provenance

`evals\teacher-v2.json` contains 18 source-grounded annotations from ten retained public documents:
15 GPT-6.1 Sol annotations carried forward from v1 and three supplemental annotations created in
Copilot Auto mode (the model identity is unavailable). Sources cover Microsoft security guidance,
NIST catalogs, public corporate strategy and US regulation. Original URLs, SHA-256 hashes,
publisher/control identifiers and attribution accompany the short, exact quotations. The Shell
strategy source is copyrighted and retained only for local evaluation.

The teacher supplied paraphrases, retrieval queries and semantic labels. Deterministic
materialization adds explicit negation, Azure scope, objective wording and prompt-injection
annotations: **108 extraction examples**, split into 24 train, 30 validation and 54 test cases.
Only original-quotation variants claim to be actual source text. Generated variants do not inherit
enterprise approval. These are **teacher-only, provisional labels**, not independent human gold.
Equivalent paraphrases retain source qualifiers and purposes, including privacy, compliance,
coverage and timing; they do not upgrade a defined baseline into an approved enterprise standard.

Splitting happens at document-family level before materialization. Logging documents train;
posture/vulnerability and corporate-strategy documents validate; incident response, network-security
editions, NIST CSF, NIST SP 800-53 and US regulation are held out. Related network editions share the
test split. Reused source URLs, original hashes, families or normalized text cannot cross splits.
The held-out set also has 38 semantic pairs, including misleading reused NS-6 identifiers and
native-mapping hard negatives. `StrategicGoal` seeds are evaluated as goals, not mislabeled as
requirements or included in requirement-only semantic pairs.
Requirement seeds without evidence of an ontology layer remain unclassified; they measure extraction,
not per-class precision. Independent labeling skill evaluations measure ontology classification.
Published NIST mappings are not semantic equivalence with Azure WAF guidance.

Verify the actual retained originals, including their bytes and every quotation:

```powershell
uv run ekb eval verify-corpus
```

This requires the previously synchronized corpus/database. Missing or changed originals fail
explicitly; normal offline evaluations use the committed attributed excerpts without downloading.
The v2 corpus verifier checks all retained original bytes and quotations before reporting coverage.
Labels remain teacher-only; additional domains broaden coverage but do not constitute independent
human review or production-grade qualification.

## A single verification command

```powershell
$env:EKB_TEST_DATABASE_URL = "postgresql+asyncpg://ekb:ekb@localhost:5432/ekb"
uv run ekb test --ui --output data\suite
```

With `--ui`, this first checks formatting, type-checks/builds the React/shadcn application and runs
Playwright against local synthetic API fixtures. It requires the Node toolchain, `npm ci` in
`frontend` and `npx playwright install chromium --only-shell`. Browser failures, missing reports
or skipped cases fail the combined result. `frontend.txt` records the frontend checks.

It then runs all unit, contract and PostgreSQL/interface integration tests, followed by extraction,
semantic-pair, retrieval and existing actual-source parser/mapping/lineage evaluations.
`suite.json`, pytest output/JUnit and detailed predictions are retained under the output directory.
Failure of either code tests or evaluation makes the command fail. Full runs reject skipped tests;
missing integration dependencies are not successful coverage.
Omit `--ui` for Python-only verification; the report explicitly identifies browser checks as
not selected. Python tests still reject committed browser assets that no longer match their source.

`--unit-only` explicitly excludes integrations and permits optional-tool skips. Default execution
uses offline rules and makes no paid calls, even if model settings exist. `--live` opts into the
configured extraction student, semantic verifier and embedding model. Bicep tests compile locally
when Azure CLI/Bicep are installed; they do not deploy or validate regional model capacity.
Ruff/basedpyright remain the separate normal contributor checks in `AGENTS.md`.
The code-test subprocess forces offline providers/local artifacts and a non-service model endpoint;
only the later live evaluation phase receives the parent process's configured paid providers.

The report's coverage map connects each capability to its tests/evals: ingestion and replay,
source evidence, structured parsing, extraction, semantic duplicates, embeddings, publication,
governance/resolution, issues, transports and infrastructure. This is a practical regression suite,
not proof that all possible document formats, adversarial inputs or enterprise behavior are covered.

## Qualification and efficiency gates

### Independently evaluating ingestion skills

```powershell
uv run ekb eval skills --skill labeling --max-concurrency 8 --output data\labeling-offline.json
uv run ekb eval skills --skill extraction --max-concurrency 8 --output data\extraction-offline.json
uv run ekb eval list
uv run ekb eval history --skill extraction
# After explicit provider/model/price configuration:
uv run --extra agent-framework ekb eval skills --live --split validation
uv run --extra agent-framework ekb eval skills --live --split test
# Explicitly authorized live performance comparison; budget covers both runs:
uv run --extra agent-framework ekb eval skills --live --compare-concurrency `
  --max-concurrency 8 --max-model-calls 10000
# Illustrative limits only: set these below each deployment's available quota and leave headroom.
uv run --extra agent-framework ekb eval skills --live --requests-per-minute 120 `
  --tokens-per-minute 600000
```

`--skill both` is the default. Labeling sees only raw segment text; extractor evaluation uses the
gold routing labels as inputs, not expected candidates, so a labeling error cannot hide extractor
behavior. Normal ingestion and the existing teacher evaluation exercise the combined model-backed
path. Prompt/model selection uses validation; reserve test for the final comparison.

Skill cases run through Pydantic Evals `Dataset.evaluate` with a typed task and an explicit
domain-specific evaluator. Existing ontology, scope, extraction, false-mandate and quality-gate
semantics remain authoritative; Pydantic Evals supplies bounded case execution and per-case
results, while pytest continues to test software and contracts. Inference still uses the injected
EKB skill port and configured provider (normally Microsoft Agent Framework); Pydantic Evals is not
an inference backend. Gold labels remain evaluator metadata and are not sent to labeling or
extraction inference.

The default is eight concurrent cases, capped at 32; selected case count is capped separately.
Pydantic Evals is submitted batches of at most twice the concurrency, so queued case tasks are
bounded as well. Labeling finishes before extraction; extraction still uses the gold routes.
Every live case reserves a conservative request allowance, including cases an adapter might
short-circuit. The built-in adapters declare a bound covering each validation attempt and all
three transport attempts; SDK retries are disabled. Replacement adapters must declare
`max_requests_per_skill` or callers must explicitly supply a safe per-case bound. An observed
overrun fails the run, but cannot undo requests a nonconforming adapter already made.

`--requests-per-minute` and `--tokens-per-minute` optionally reserve quota immediately before
each provider request, including every retry, per deployment over a rolling minute. This is separate
from the per-case call budget. The token reservation uses the configured model context limit per
request; it is a conservative estimate, not measured provider usage. The same limiter is shared
across cases, skills and comparison arms, with a cooldown between arms. Rate-limited live runs
require an inference adapter implementing the request-control contract; use a dedicated adapter
instance for the evaluation.
Limits apply only within this evaluation process: leave headroom for other clients and use
provider-level controls to coordinate aggregate quotas. A single request whose estimated allowance
exceeds a configured limit is rejected before inference. Concurrency alone is not rate limiting, and
neither reservations nor post-run quality/cost gates are a provider billing cap.

Reports distinguish observed requests and retries from estimated rate-limit reservations.
Provider-returned model IDs and cached-input token counts are retained when available; the actual
model version, cache counts and throttles remain explicitly unknown when unavailable. A failed
request may have consumed tokens even if it returned no usage: report total token usage and priced
cost as unknown in that case, while retaining known partial counts. Cost is calculated from
reported tokens and configured prices, not an invoice; cached-token discounts are not inferred.
The current evaluator is deterministic and makes zero judge requests.

Each run atomically publishes a uniquely named, create-only JSON report and compact index under
`data\evaluation-history` by default; `--history-dir` can select another durable location, while
`--output` is maintained as a latest-report alias and cannot overwrite a retained report or input.
Completed, partial, failed and cancelled runs retain their status. If index publication is
interrupted, the catalog derives its entry from the immutable report; corrupt indexes or missing
reports fail explicitly. Point `eval list --history-dir` and
`eval history --history-dir` at the same location to inspect custom history. The index lets
`eval list` show each bundled dataset/skill, including never-run combinations. `eval history`
filters history by dataset or skill and reports deployment, actual IDs, quality and performance
over time. Reports include dataset/gate hashes, evaluator and skill fingerprints, configuration,
code revision/dirty state and timestamps.
Do not edit retained reports; use a new run to preserve comparison history. The optional
`--compare-concurrency` runs sequential and bounded-parallel arms only with explicit `--live`
authorization and sufficient budget for equivalent work. It records both child reports and the
comparison; interrupted comparisons retain their status and links to any started child runs.
It reports speedup only when both arms completed and their datasets, split, gates,
skill/evaluator definitions, provider/deployment conditions, case-level quality and measured
provider identities and cached-input profiles agree. Missing provider model identity for any model
request or unknown cache accounting makes the comparison incomparable, not implicitly cold.
Provider-reported model identifiers are not necessarily immutable model versions; versions remain
unknown unless explicitly available. Offline, live without cached input, live with cached input and
unknown-cache runs are identified separately; there is no skill-evaluation-layer cache. A comparison
may incur provider cost. No live comparison was authorized for this implementation, so live speedup,
throttling and cost remain unverified.

### Document classification and mixed ontology layers

```powershell
uv run ekb eval documents --output data\document-evaluation.json
uv run ekb eval documents --verify-sources
# Explicitly configured provider; may incur cost:
uv run --extra agent-framework ekb eval documents --live
```

`evals\documents-v1.json` contains provisional reference labels for three document samples.
Organizational coverage is scored independently of ontology class and named scope:

| Sample | Coverage | Expected content |
|---|---|---|
| Shell annual report | Company level | Strategic goals and explicit Shell organizational context |
| NIST CSF 2.0 | Company level, generically | Strategic requirement, three architecture domains, conceptual and technical requirements |
| ISO/IEC 27001 metadata | Unknown | No invented ISO clauses or requirement candidates |

Company-level coverage does not mean every passage has one abstraction level. NIST is the publisher,
not the organization to which a requirement is automatically applied. No enterprise adoption,
Shell applicability, platform specificity or mandatory wording is inferred from a standard's name.
Architecture domains remain context labels, not extracted requirements. Shell's explicit vision
is distinguished from a binding target; the competitiveness interpretation remains provisional.

The ISO case uses repository-authored metadata, as explicitly selected by the user. No licensed
standard was available and ISO's official public overview returned HTTP 403. This negative checks
the metadata boundary; it provides **no evidence of normative ISO extraction accuracy**.

The same labeling/extraction ports, grounding gates and metrics are reused. The report adds
coverage accuracy, per-document missing/unexpected classes and named scopes, exact class-set
accuracy and correctly empty extraction cases. Extraction receives reference routing independently
of predicted labels; it is not an end-to-end ingestion quality score. `--verify-sources` checks
original PDF bytes and each cited physical page in both text reading orders. Repository metadata
uses an explicitly recorded LF-normalized text hash for checkout-independent verification.
Without that flag, committed passages run offline without requiring or downloading originals.

The annual-report family is validation, NIST is test and metadata is train. `eval documents`
runs all three as a diagnostic, never model qualification; annotations never enter labeling prompts.
The normal `uv run ekb test` suite includes this dataset and retains `documents.json`.

The measured offline Rules baseline verified all 11 passages across the three retained sources.
Label identification was 1 TP / 0 FP / 8 FN (recall .111); gold-routed extraction was
1 TP / 0 FP / 4 FN (recall .200). Organizational coverage was 0/3: Rules does not implement that
semantic classification. Only the metadata sample had an exact ontology class-set match; its
empty extraction was correctly retained. These results expose missing classification behavior,
not model qualification or successful company/NIST classification. No live model was measured.

### NIST CSF Core extraction

```powershell
uv run ekb eval nist
uv run ekb eval nist --excerpts-only
uv run ekb eval nist --excerpts-only --predictions evals\nist-csf\predictions_sample.json
# Explicitly configured provider; may incur cost:
uv run --extra agent-framework ekb eval nist --model-profile config\foundry.yaml --live
```

`evals\nist-csf` imports the supplied draft reference JSON unchanged: 6 Functions, 22 Categories,
106 Subcategories, 25 provisional ontology mappings and 14 authored negative claims. These
are source taxonomy and provisional annotations, not additional OWL classes or reviewed gold.
The sample predictions are intentionally incomplete scorer input, not measured model results.
`archive-provenance.json` records original hashes and decisions; `source-excerpts.json` separately
retains all 134 native nodes with exact whitespace-normalized, per-page quotations audited against
the retained NIST CSWP 29 PDF. Source review found **25 incorrect structural page locators**,
usually subcategories continuing beyond their category's first page. Reports list corrections
without silently rewriting the imported references.

The default command verifies the retained PDF hash and runs native parsing; `--excerpts-only`
replays committed passages without a fresh original-source audit. Whole-publication ingestion
detects the actual cover identifier/title, preserves native hierarchy and IDs in Core segments,
and retains surrounding publication text. Explicit native-only parsing uses
`framework_namespace: nist-csf`; page subsets use generic PDF parsing.
The shared labeling/extraction ports process the 25 curated outcomes using **source text only**,
not expected classes or normalized answers. Framework conditions need no imperative wording;
taxonomy is not a goal, an ontology layer or enterprise adoption. Grounding also rejects invented
numeric qualifiers such as unsupported protocol versions and encryption strengths.

Measured offline results on the actual PDF:

| Check | Result |
|---|---|
| Native Function / Category / Subcategory identities | 6/6, 22/22, 106/106; no false positives |
| Native parent links | 128/128 |
| Curated outcomes identified / source-verified | 25/25, 25/25 |
| Correct provisional ontology classes | 0/25; Rules leaves these unknown |
| Explicit negative decisions | 14 NOT RUN; missing decisions never count as rejection |
| Unchanged repeat | Parse cache hit, 25 extraction cache hits, zero model calls |

A structural before/after comparison used the existing generic full-page parser as baseline:
492 generic segments carried no native identities or source-keyed outcomes. Native detection
produced 134 native segments plus 271 surrounding segments, and Rules preserved all 106 outcome
source keys. This measures structural extraction improvement, **not semantic classification**.
The generic framework prompt improvements remain unverified by a live model. Cached local
GPU/NPU model loading failed and the user selected offline continuation; no model was downloaded
and no paid inference was run.

JSON/Markdown history under `data\nist-evaluations` records fingerprints, native nodes, predictions,
class errors, scope/adoption/authority checks, exact evidence, duplicates, negative decision status,
current usage and cache hits. Prior-run class deltas require the same source, reference fingerprint,
model and source mode. All runs remain unqualified pending independent review; offline success
means structural, extraction and safety checks passed, not successful ontology classification.
The normal verification suite includes committed-excerpt replay under its `nist` output folder.

Two reference disagreements are deliberately retained: the user permits strategic requirement
**candidates** without inferring adoption, unlike the archive's blanket type prohibition.
Also, `PR.DS-01` is `TechnicalRequirement` in the supplied reference but `ConceptualRequirement`
in the earlier document case. Neither provisional label is silently changed.

### Enterprise strategic-goal evaluation

```powershell
uv run ekb eval goals --compare-strategies
uv run ekb eval goals --strategy hierarchical --model-profile config\foundry.yaml --live
uv run ekb eval report --latest
```

`evals\goals\shell-annual-2025.json` stores provisional, source-grounded reference annotations,
including five goal interpretations, subordinate targets and non-goal examples. The evaluator
checks the source hash, PDF page count and all 20 evidence anchors against their cited pages before
scoring. Whole-document, section-aware and hierarchical strategies share parse/extraction caches;
the report scores detection, ontology type, abstraction, scope, duplicates, provenance and
provisional hierarchy hypotheses, and records tokens, latency, request counts and available cost.
Semantic fidelity remains explicitly marked for human review. Model comparisons require separately
configured profiles and `--live`; an unavailable or unconfigured provider is not a successful run.

The measured offline Rules baseline and one general-rule iteration were:

| Strategy | Pages | Primary TP / FP / FN | Precision / recall / F1 | Input / output tokens |
|---|---:|---:|---:|---:|
| Whole-document | 474 | 1 / 191 / 2 | .005 / .333 / .010 | 0 / 0 |
| Section-aware | 13 | 1 / 7 / 2 | .125 / .333 / .182 | 0 / 0 |
| Hierarchical (offline exact deduplication) | 13 | 1 / 7 / 2 | .125 / .333 / .182 | 0 / 0 |

Before the explicit-vision rule, the section-aware offline baseline returned 0 of 3 primary
references, 7 false positives and 3 false negatives. The iteration recovered the explicitly stated
enterprise vision; it still misses two primary goals, and alternatives such as Goal Zero remain
unresolved. Whole-document extraction produced 192 strategic-goal candidates and remains too noisy.
Hierarchical offline mode performs exact deterministic deduplication only; it is not evidence that
model synthesis improves quality. The new prompt guidance was not evaluated by RulesInference,
which ignores model prompts. No small/strong model comparison or live cost/latency result is claimed:
the default profile is offline rules, and paid model calls were not run.

Structured JSON and Markdown artifacts are written under the selected `--output` directory. These
results are provisional diagnostics, not production qualification; independent human review remains
necessary.

`evals\skills-v1.json` contains 16 provisional cases: three attributed excerpts reused verbatim
from the existing teacher corpus, retaining its family/split/hash/attribution, and 13 neutral
synthetic cases. Cases cover mixed organizational scope, six semantic types, unknown layer,
negation/exception/numeric qualifiers, domain-only content and an untrusted annotation.
Families, original URLs/hashes and duplicate text cannot cross splits. These are regression and
provisional qualification cases, not independent human gold or production coverage.

Each skill reports its own fingerprint, precision/recall, errors, false mandates, p95 latency,
tokens and priced cost, using the existing gates below. Label accuracy includes ontology type
and scope; extraction matching additionally requires ontology type, modality and retained qualifiers.
`qualified` means only that a live held-out run met these **provisional** gates. Sparse or
domain-only input deliberately needs no extraction call. Unknown/missing prices and unmetered
model calls cannot pass cost gates. `EKB_LABELING_MODEL_ID` optionally selects a different labeler;
set its `EKB_LABELING_INPUT_PRICE` / `EKB_LABELING_OUTPUT_PRICE` separately.

`uv run ekb test` now also runs both offline skill evaluations and persists `skills.json`.
Offline pass/fail covers software/safety, never model precision qualification. Cost and latency
gates remain post-run measurements, not hard provider spending caps.

### End-to-end model qualification

```powershell
uv run ekb eval qualify --output data\qualification-offline.json
uv run ekb eval qualify --live --split validation --output data\qualification-validation.json
uv run ekb eval qualify --live --split test --output data\qualification-test.json
```

Select/configure models before using the live examples. Expected labels are not included in
student/verifier requests; structural short-circuits depend on input evidence, never expected
answers. Retrieval measures generated queries against held-out original-quotation candidates.
Embedding neighbors are retrieval proposals; they do not establish equivalence or authority.

`evals\gates.json` versions provisional requirements:

| Measure | Required |
|---|---|
| Extraction precision / recall | >= .90 / .85 |
| Scope / modality accuracy | >= .90 each |
| Semantic-pair accuracy | >= .85 |
| Retrieval Recall@3 / MRR | >= .90 / .75 |
| False mandates / false equivalence / provider errors | 0 |
| Exact grounding, parser/mapping/lineage safety | pass |
| Extraction/comparison p95 and total embedding batch latency | <= 15 seconds each |
| Total reported tokens / estimated run cost | <= 100,000 / 1 currency unit |

Supply per-million-token prices in consistent currency:
`EKB_MODEL_INPUT_PRICE`, `EKB_MODEL_OUTPUT_PRICE`, `EKB_EMBEDDING_INPUT_PRICE`.
Unknown cost cannot pass as zero. The two chat deployments share configured token prices; when
their prices differ, use a conservative upper bound for both. Repeated reports retain dataset
hash, prompt, student, verifier/embedding fingerprints, thresholds, usage and case predictions.
Only passing **live test-split** reports set `student_meets_provisional_gates=true`.
Missing/zero token accounting for model calls also prevents cost qualification; reported token
totals are not treated as complete merely because the provider omitted its usage object.

Use validation for model/prompt selection and threshold calibration; reserve test for final
comparison. Do not select a model by repeatedly tuning against held-out test results. Version
labels/gates when reviewed or expanded. Small teacher-only sets need independent review and more
document families before production qualification.

The historical v2 **offline rules/lexical diagnostic**, before the ontology vocabulary transition, was:

| Measure | Result |
|---|---|
| Requirement precision / recall | 1.00 / .467 |
| Objective precision / recall | 1.00 / 1.00 |
| Scope / known-scope accuracy | .70 / .00 |
| Semantic-pair accuracy | .711 |
| Lexical retrieval Recall@3 / MRR | .50 / .556 |
| Safety checks | pass |
| Live student qualified | **No** |

The deterministic baseline passes safety checks but misses recall, scope, semantic and retrieval
quality thresholds. No live model or embedding calls were made; usage/cost qualification remains
unverified, not zero. The configured cheap student therefore has **no quality qualification**.
Live validation on the expanded set and a single held-out test run remain necessary; do not treat
these offline results as model performance.

Implementation verification: the combined full profile passed **158 code tests, zero skips**
and all offline safety evals. It also passed with paid-model settings present but a deliberately
unreachable model endpoint, demonstrating that default verification does not call those models.
Ruff, formatting, basedpyright, Alembic drift/lock checks and local Bicep compilation passed.
Detailed local evidence is under `data\frugal-suite-final`; runtime artifacts are not committed.

`--max-cases` rejects oversized extraction+pair workloads before calls. Cases are sequential,
provider batching/retries remain bounded, and normal inference uses existing context/output limits.
Case budgets are **not spending caps**: retries may add requests, and token/cost/latency checks are
post-run estimates, not provider invoices. Grounding verifies substrings, not semantic entailment.

## Training-only distillation export

```powershell
uv run ekb eval distill --output data\distillation\train-v2.jsonl
```

This produces 24 OpenAI-format supervised examples from **train only**, plus source/case/teacher
provenance metadata. Existing files are not overwritten. No validation/test export option exists.
No fine-tuning job is started, and exporting labels does not demonstrate student capability.

`--teacher-model YOUR_ACTUAL_TEACHER_DEPLOYMENT` explicitly requests fresh training-set labels
through the configured OpenAI-compatible endpoint. The teacher sees raw inputs, not expected
answers; its output must pass schema/exact-grounding validation. Configure endpoint, token
parameter, temperature, output budget and prices for that teacher before running. There is no
guessed GPT-6.1 Foundry deployment or background paid generation.

## Minimal infrastructure and embedding integration

See `infra\README.md` for Entra-only Foundry Bicep, required student model/version selection,
small embedding deployment, optional project/RBAC and an optional teacher that is off by default.
No Azure AI Search, vector database, GPU VM or always-on compute is required.

Configure `EKB_INFERENCE=openai`, actual deployment IDs in `EKB_MODEL_ID`,
`EKB_RECONCILIATION_MODEL_ID`, `EKB_EMBEDDING_MODEL_ID`, and the account's OpenAI-compatible
`EKB_MODEL_ENDPOINT`. Leave the API key unset for the existing Azure credential flow. Models that
require it can use `EKB_MODEL_MAX_TOKEN_PARAMETER=max_completion_tokens` and
`EKB_MODEL_TEMPERATURE=null`; temperature is then omitted entirely.

The generic semantic stage combines lexical candidates with cosine neighbors from an independently
selected, stable embedding pool. Defaults are 128 assertions and five neighbors, configurable via
`CheckOptions.embedding_pool_size` and `embedding_neighbors`, with existing candidate limits.
This discovers paraphrases with no lexical overlap, while durable provider/input-keyed PostgreSQL
vectors and grounded judgments avoid repeated paid work. Pool coverage is bounded and not exhaustive;
it is not a whole-corpus ANN index. Matches remain reversible review proposals, never automatic merges.
