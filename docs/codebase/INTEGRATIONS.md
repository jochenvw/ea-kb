# External Integrations

## 1) Integration Inventory

| System | Type/purpose | Auth model | Runtime requirement | Evidence |
|---|---|---|---|---|
| PostgreSQL | Knowledge, evidence, tasks and replay | Connection URL | Ingestion/inspection | PostgreSQL adapter |
| Official publishers | HTTP source retrieval | Public HTTP(S) | Selected manifests only | Source adapter, public manifest |
| OpenAI-compatible endpoints | Grounded model extraction | API key, Azure credential or local access | Optional; rules are default | Inference adapter |
| Agent Framework | Alternative model client | Same model configuration | Optional package/provider | `AgentFrameworkInference` |
| Azure Blob | Original-document storage | Async `DefaultAzureCredential` | Alternative to filesystem | Artifact adapter |
| Application Insights | Trace/log export | Monitor connection string | Optional exporter | Observability |
| MCP client | Read-only stdio protocol | Local process/transport access | Optional inspection | MCP interface |

There is no configured API gateway, service mesh, external queue or event bus. Web/worker
coordinate through PostgreSQL, not a broker.

## 2) Data Stores

| Store | Role/access layer | Operational limit | Evidence |
|---|---|---|---|
| PostgreSQL | Authoritative rows/JSONB through unit-of-work repositories | Disposable schema before v1; migrations from v1 | PostgreSQL models/repositories |
| Filesystem | Content-addressed originals through artifact port | Hard-link support; backup with database | Artifact adapter |
| Azure Blob | Alternative artifact-port implementation | Provision container and identity first | Artifact adapter |
| Telemetry/debug files | Observability/inference adapters | Operator-owned retention and permissions | Observability, `Settings` |
| Parser corpus | Standalone originals/retrieval index | Not automatic pipeline ingestion | Downloader |

## 3) Secrets and Credentials Handling

Environment, `.env`, profiles and optional file-secret settings feed `Settings`; secret fields use
`SecretStr`. `.env` variants and runtime data are ignored. Compose contains documented development
database credentials; Azure examples are placeholders. Automated secret rotation is not implemented.

## 4) Reliability and Failure Behavior

The application retries transient HTTP failures with bounded backoff/jitter. Source HTTPX uses a
90-second default timeout and 15-second connect timeout; the direct model client uses 180 seconds.
Conditional retrieval, payload checks, byte limits and durable checkpoints avoid unnecessary work
and false success. The standalone downloader records failures and exits nonzero.
No circuit breaker, distributed failover or automatic paid-model fallback exists. Rules are an
explicit provider choice, not a silent fallback after model failure.

## 5) Observability for Integrations

OpenTelemetry spans cover retrieval, parsing, extraction, validation, retries and transactions.
Local rotated JSONL is configured; Azure settings add exporters. Usage totals cover committed
validated calls, not every provider-billable attempt. Offline model/Azure contracts do not establish
live connectivity, identity permissions, service-level objectives or exporter delivery.

## 6) Evidence

- `src\ekb\adapters\sources\__init__.py`, `src\ekb\adapters\inference\__init__.py`.
- `src\ekb\adapters\artifacts\__init__.py`, `src\ekb\adapters\persistence\postgres\__init__.py`.
- `src\ekb\services\retry.py`, `src\ekb\observability\__init__.py`, `src\ekb\config\__init__.py`.
- `.env.example`, `.gitignore`, `compose.yaml`, `config\azure.yaml`.
- `deploy\azure\container-apps.env.example`, `sources\public.yaml`, `scripts\download_test_corpus.py`.
