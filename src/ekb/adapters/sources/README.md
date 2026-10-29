# Source connectors and plugins

`__init__.py` implements filesystem discovery, bounded HTTP retrieval, manifest loading and plugins.
`benchmarks.py` explicitly syncs official Learn Markdown/HTML, discovered workbooks and policy JSON.

- `SourceConnector.discover` yields stable items; `fetch` returns bytes or explicit not-modified.
- Preserve revision indicators and conditional headers; inaccessible discovery is not mass deletion.
- `ekb.connectors` entry points map provider names to factories loaded by `load_plugin`.
- Runtime currently selects built-ins explicitly; registering a plugin does not activate it.
- Benchmark sync checkpoints named raw/normalized artifacts and verifies hashes before 304 reuse.
- Cached benchmark discovery/fetch uses no network; focused sync retains previously known assets.
- Native catalog manifests declare canonical namespace/revision (e.g. `nist-sp-800-53`, `5`).

Start with `tests\unit\test_sources.py` and plugin checks in `tests\unit\test_adapters.py`.
See `docs\plugins.md` before introducing a connector or changing failure semantics.
