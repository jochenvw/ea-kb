# Ingestion source manifests

`public.yaml` lists selected official publications for the application's HTTP/OSCAL connector.

- Entries carry stable IDs, formats, publisher/access notes, intended scope and optional selections.
- ISO entries are metadata only; never supply fabricated clauses or redistribute licensed text.
- Run `uv run ekb scan --manifest sources\public.yaml` from the repository root.
- Benchmark entries document opt-in `ekb sources sync microsoft-security-benchmark` seeds.
  After sync, no-argument `ekb scan` ingests the validated cache without network or model calls.
  Manifest routing keeps these assets separate from ordinary HTTP sources.

Schema/loading live in domain `SourceSpec` and the source adapter.
The standalone download collection instead uses `config\test_corpus.yaml`.
