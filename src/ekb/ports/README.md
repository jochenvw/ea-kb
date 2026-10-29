# Interchangeable application contracts

`__init__.py` defines small typed `Protocol` interfaces for sources, artifacts, parsing, inference,
repositories and explicit units of work.
`ArtifactStore` separates immutable content-addressed originals from named corpus views/checkpoints.
`SemanticVerifier` and `EmbeddingPort` are opt-in boundaries; `BenchmarkRepository` preserves
source-bound comparisons and native identifier resolution without equating mapping with coverage.
`workflow.py` defines stage/check contracts and execution, resolution and issue repositories.
Future stores must preserve these semantics through `tests\contract\test_resolution.py`.

These contracts use domain values; do not import concrete adapters or provider/transport SDKs.
Add or extend a port only for a real interchangeable boundary, not every helper.
Wire implementations in `src\ekb\runtime.py` and preserve behavior with adapter/repository tests.
See `docs\plugins.md`; strict typing applies to this package.
