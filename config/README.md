# Runtime profiles and corpus configuration

- `local.yaml` and `azure.yaml` supply defaults to `Settings`; environment values take precedence.
- `test_corpus.yaml` belongs to the standalone downloader, not the ingestion source connector.
- Select a runtime profile with `EKB_CONFIG_FILE`; never put real credentials in these files.

Settings fields and precedence live in `src\ekb\config\__init__.py`.
See the root README for setup and `sources\public.yaml` for the ingestion manifest.
