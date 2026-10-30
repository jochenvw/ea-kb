# Runtime profiles and corpus configuration

- `local.yaml` and `azure.yaml` supply defaults to `Settings`; environment values take precedence.
- `foundry.yaml` keeps local storage but opts into Entra-authenticated student/embedding models.
  Supply the HTTPS OpenAI v1 endpoint; defaults never silently activate paid models.
- Model-backed profiles prefer Microsoft Agent Framework (`uv sync --extra agent-framework`).
  `labeling_model_id` optionally selects a cheaper labeler; its own prices are required for cost gates.
- `test_corpus.yaml` configures the standalone corpus and its `--benchmark` application-sync route.
- Select a runtime profile with `EKB_CONFIG_FILE`; never put real credentials in these files.

Settings fields and precedence live in `src\ekb\config\__init__.py`.
See the root README for setup and `sources\public.yaml` for the ingestion manifest.
