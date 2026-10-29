# Original-document artifact storage

`__init__.py` provides filesystem and asynchronous Azure Blob implementations of `ArtifactStore`.

- Keys are SHA-256 digests; reads verify integrity.
- Filesystem writes publish once with hard links; the storage volume must support them.
- Azure uses a pre-existing container and configured identity permissions.

Preserve idempotence and historical originals when adding a provider.
See `tests\unit\test_adapters.py` and `docs\plugins.md`; storage changes do not migrate old files.
