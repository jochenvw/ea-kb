# Ingestion source manifests

`public.yaml` lists selected official publications for the application's HTTP/OSCAL connector.

- Entries carry stable IDs, formats, publisher/access notes, intended scope and optional selections.
- ISO entries are metadata only; never supply fabricated clauses or redistribute licensed text.
- Run `uv run ekb scan --manifest sources\public.yaml` from the repository root.
- Benchmark entries document opt-in `ekb sources sync microsoft-security-benchmark` seeds.
  They remain disabled for generic ingestion until the dedicated structured ingestion route lands.

Schema/loading live in domain `SourceSpec` and the source adapter.
The standalone download collection instead uses `config\test_corpus.yaml`.
