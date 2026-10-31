# Ingestion source manifests

`public.yaml` lists selected official publications for the application's HTTP/OSCAL connector.

- Entries carry stable IDs, formats, publisher/access notes, intended scope and optional selections.
- ISO entries are metadata only; never supply fabricated clauses or redistribute licensed text.
- Run `uv run ekb scan --manifest sources\public.yaml` from the repository root.
- Security guidance entries include direct article URLs: an overview alone does not ingest linked pages.
- `extract_strategy: false` keeps corroborating publications without duplicating company strategy.
- All entries use ordinary HTTP/OSCAL retrieval and the shared ontology pipeline.

Schema/loading live in domain `SourceSpec` and the source adapter.
The standalone download collection instead uses `config\test_corpus.yaml`.
