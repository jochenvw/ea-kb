# Interchangeable application contracts

`__init__.py` defines small typed `Protocol` interfaces for sources, artifacts, parsing, inference,
repositories and explicit units of work.

These contracts use domain values; do not import concrete adapters or provider/transport SDKs.
Add or extend a port only for a real interchangeable boundary, not every helper.
Wire implementations in `src\ekb\runtime.py` and preserve behavior with adapter/repository tests.
See `docs\plugins.md`; strict typing applies to this package.
