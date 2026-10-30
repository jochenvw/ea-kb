# Source connectors and plugins

`__init__.py` implements filesystem discovery, bounded HTTP retrieval, manifest loading and plugins.

- `SourceConnector.discover` yields stable items; `fetch` returns bytes or explicit not-modified.
- Filesystem discovery inventories all non-symlink files; unsupported formats are metadata-only.
- Preserve revision indicators and conditional headers; inaccessible discovery is not mass deletion.
- HTTP retrieval owns bounded transient retries; `SourceMissingError` comes from the shared port.
- `ekb.connectors` entry points map provider names to factories loaded by `load_plugin`.
- Runtime currently selects built-ins explicitly; registering a plugin does not activate it.
- Publisher-specific documents are ordinary manifest sources, not special connector types.
- Native catalog manifests declare canonical namespace/revision (e.g. `nist-sp-800-53`, `5`).

Start with `tests\unit\test_sources.py` and plugin checks in `tests\unit\test_adapters.py`.
See `docs\plugins.md` before introducing a connector or changing failure semantics.
