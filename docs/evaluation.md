# Source-grounded, frugal evaluations

EKB separates **offline software/safety verification** from **live model qualification**.
One passing offline command does not establish that a cheap model or embedding deployment works.
No live model or Azure deployment is claimed by this implementation.

## Dataset and teacher provenance

`evals\teacher-v1.json` contains 15 GPT-6.1 Sol teacher annotations from seven retained public
Microsoft/NIST documents. The teacher was the interactive Copilot model, `gpt-6.1-sol`; this is
not a claim that the same identifier can be deployed in Foundry. Original URLs, SHA-256 hashes,
publisher/control identifiers and attribution accompany the short, exact quotations.

The teacher supplied paraphrases, retrieval queries and semantic labels. Deterministic
materialization adds explicit negation, Azure scope, objective wording and prompt-injection
annotations: **90 extraction examples**, split into 24 train, 24 validation and 42 test cases.
Only original-quotation variants claim to be actual source text. Generated variants do not inherit
enterprise approval. These are **teacher-only, provisional labels**, not independent human gold.
Equivalent paraphrases retain source qualifiers and purposes, including privacy, compliance,
coverage and timing; they do not upgrade a defined baseline into an approved enterprise standard.

Splitting happens at document-family level before materialization. Logging documents train;
posture/vulnerability documents validate; incident response, network-security editions and native
NIST boundary protection are held out. Related network editions share the test split. Reused source
URLs, original hashes, families or normalized text cannot cross splits. The held-out set also has
30 semantic pairs, including misleading reused NS-6 identifiers and native-mapping hard negatives.
Published NIST mappings are not semantic equivalence with Azure WAF guidance.

Verify the actual retained originals, including their bytes and every quotation:

```powershell
uv run ekb eval verify-corpus
```

This requires the previously synchronized corpus/database. Missing or changed originals fail
explicitly; normal offline evaluations use the committed attributed excerpts without downloading.
The verification performed for v1 established **7 source hashes and 15 exact quotations**.

## A single verification command

```powershell
$env:EKB_TEST_DATABASE_URL = "postgresql+asyncpg://ekb:ekb@localhost:5432/ekb"
uv run ekb test --output data\suite
```

This runs all unit, contract and PostgreSQL/interface integration tests, then extraction,
semantic-pair, retrieval and existing actual-source parser/mapping/lineage evaluations.
`suite.json`, pytest output/JUnit and detailed predictions are retained under the output directory.
Failure of either code tests or evaluation makes the command fail. Full runs reject skipped tests;
missing integration dependencies are not successful coverage.

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

The measured v1 **offline diagnostic** was:

| Measure | Offline rules / lexical baseline |
|---|---|
| Extraction precision / recall | 1.00 / .50 |
| Semantic-pair accuracy | .70 |
| Retrieval Recall@3 / MRR | .429 / .50 |
| Safety checks | pass |
| Live student qualified | **No** |

Thus the baseline is safe enough for deterministic software checks but insufficient for the
requested model-quality gates. No cheap student or real embedding model has yet been measured.
Live qualification is the required next evidence, not an assumed outcome.

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
uv run ekb eval distill --output data\distillation\train-v1.jsonl
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
