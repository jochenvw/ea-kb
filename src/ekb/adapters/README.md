# Infrastructure adapters and plugin implementations

- `sources` implements filesystem/HTTP discovery, retrieval and `ekb.connectors` plugin loading.
- `parsers` turns original documents into stable, located text segments.
- `inference` implements offline rules and configured model transports.
- `artifacts` preserves content-addressed originals; `persistence` owns database repositories.

Implement contracts in `src\ekb\ports\__init__.py`; wire providers in `src\ekb\runtime.py`.
Entry-point registration does not automatically activate a connector or grant network access.
See `docs\plugins.md`; adapter/contract verification lives under `tests\unit` and `tests\contract`.
