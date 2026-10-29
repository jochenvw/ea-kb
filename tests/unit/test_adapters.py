import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ekb.adapters.artifacts import AzureBlobArtifactStore, FilesystemArtifactStore
from ekb.adapters.sources import connector_plugins, load_plugin, read_manifest
from ekb.config import Settings
from ekb.domain.models import content_hash
from ekb.observability import ApplicationTracer, JSONLSpanExporter


async def test_filesystem_artifacts_are_content_addressed_and_integrity_checked(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    key = await store.put(b"source evidence")
    assert key == content_hash(b"source evidence")
    assert await store.put(b"source evidence") == key
    assert await store.get(key) == b"source evidence"
    with pytest.raises(ValueError):
        await store.get("../escape")
    import asyncio

    await asyncio.to_thread(store._path(key).write_bytes, b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        await store.get(key)


async def test_concurrent_artifact_publication_is_create_once(tmp_path):
    import asyncio

    store = FilesystemArtifactStore(tmp_path / "artifacts")
    for iteration in range(20):
        content = f"Synthetic concurrent evidence {iteration}".encode()
        results = await asyncio.gather(
            *(store.put(content) for _ in range(4)), return_exceptions=True
        )
        assert all(isinstance(result, str) for result in results), results
        keys = [result for result in results if isinstance(result, str)]
        assert len(set(keys)) == 1
        assert await store.get(keys[0]) == content
        assert await store.put(content) == keys[0]


async def test_named_artifacts_advance_without_changing_originals(tmp_path):
    store = FilesystemArtifactStore(
        tmp_path / "artifacts", document_directory=tmp_path / "benchmark"
    )
    original = await store.put(b"original")
    await store.put_named("mcsb_v2/raw/source.md", b"original")
    await store.put_named("manifest.json", b'{"revision":1}')
    await store.put_named("manifest.json", b'{"revision":2}')
    assert await store.get_named("manifest.json") == b'{"revision":2}'
    assert await store.get_named("mcsb_v2/raw/source.md") == b"original"
    assert await store.get(original) == b"original"
    for key in ("../escape", "/absolute", "C:\\outside", "a//b", "a/../b"):
        with pytest.raises(ValueError):
            await store.put_named(key, b"not written")
    with pytest.raises(FileNotFoundError):
        await store.get_named("missing.json")


async def test_named_checkpoint_retries_short_windows_reader_locks(tmp_path, monkeypatch):
    import os

    original = os.replace
    attempts = 0

    def replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("Synthetic Windows reader lock")
        original(source, target)

    monkeypatch.setattr("ekb.adapters.artifacts.os.replace", replace)
    store = FilesystemArtifactStore(tmp_path)
    await store.put_named("manifest.json", b'{"revision":2}')
    assert await store.get_named("manifest.json") == b'{"revision":2}'
    assert attempts == 3


async def test_azure_artifact_adapter_contract_without_cloud_credentials():
    blob = MagicMock()
    blob.upload_blob = AsyncMock(side_effect=[None, ResourceExistsError("Already stored")])
    download = MagicMock()
    download.readall = AsyncMock(return_value=b"source evidence")
    blob.download_blob = AsyncMock(return_value=download)
    client = MagicMock()
    client.get_blob_client.return_value = blob
    client.close = AsyncMock()
    store = AzureBlobArtifactStore("https://example.blob.core.windows.net", "test", client=client)
    key = await store.put(b"source evidence")
    assert await store.put(b"source evidence") == key
    assert await store.get(key) == b"source evidence"
    blob.upload_blob.assert_called_with(b"source evidence", overwrite=False)
    await store.close()
    client.close.assert_awaited_once()


async def test_azure_named_documents_match_filesystem_checkpoint_contract():
    blob = MagicMock(upload_blob=AsyncMock(), download_blob=AsyncMock())
    stream = MagicMock(readall=AsyncMock(return_value=b'{"revision":2}'))
    blob.download_blob.return_value = stream
    client = MagicMock()
    client.get_blob_client.return_value = blob
    store = AzureBlobArtifactStore("https://example.blob.core.windows.net", "test", client=client)
    await store.put_named("manifest.json", b'{"revision":2}')
    assert await store.get_named("manifest.json") == b'{"revision":2}'
    client.get_blob_client.assert_called_with(
        "test", "test-corpus/public/microsoft_security_benchmark/manifest.json"
    )
    blob.upload_blob.assert_awaited_once_with(
        b'{"revision":2}',
        overwrite=True,
        metadata={"sha256": content_hash(b'{"revision":2}')},
    )
    blob.download_blob.side_effect = ResourceNotFoundError("missing")
    with pytest.raises(FileNotFoundError):
        await store.get_named("missing.json")
    with pytest.raises(ValueError):
        await store.put_named("../outside", b"not written")


def test_installed_connector_entry_points_are_loadable(tmp_path):
    assert {
        "filesystem",
        "http",
        "oscal",
        "microsoft-security-benchmark",
    } <= connector_plugins().keys()
    connector = load_plugin("filesystem", {"root": tmp_path})
    assert connector.name.startswith("filesystem:")
    with pytest.raises(ValueError):
        load_plugin("unavailable-proprietary-integration", {})


def test_public_manifest_has_access_and_applicability_metadata():
    from pathlib import Path

    entries = read_manifest(Path("sources/public.yaml"))
    assert {"asb-v2", "mcsb-v1", "mcsb-v2"} <= {entry.id for entry in entries}
    assert all(e.publisher and e.access_notes and e.intended_scope and e.id for e in entries)
    assert all(e.metadata_only for e in entries if e.id.startswith("iso-"))
    assert all(e.jurisdiction for e in entries if e.category.startswith("regulation"))


def test_profile_yaml_loads_but_environment_wins(tmp_path, monkeypatch):
    profile = tmp_path / "profile.yaml"
    profile.write_text("scan_concurrency: 3\nocr: false\n")
    monkeypatch.setenv("EKB_CONFIG_FILE", str(profile))
    monkeypatch.setenv("EKB_SCAN_CONCURRENCY", "5")
    assert Settings().scan_concurrency == 5


def test_exception_messages_and_source_text_do_not_leak_into_traces(tmp_path, monkeypatch):
    memory = InMemorySpanExporter()
    disk = JSONLSpanExporter(tmp_path / "traces.jsonl", 4096, 2)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    provider.add_span_processor(SimpleSpanProcessor(disk))
    monkeypatch.setattr(
        "ekb.observability.trace.get_tracer", lambda *args: provider.get_tracer("test")
    )
    tracer = ApplicationTracer()
    try:
        with pytest.raises(ValueError):
            with tracer.start_as_current_span("parsing", attributes={"source.id": "test"}):
                raise ValueError("confidential source text and fake-secret")
        provider.force_flush()
        spans = memory.get_finished_spans()
        assert spans[0].attributes is not None
        assert spans[0].attributes["error.type"] == "ValueError"
        assert not spans[0].events
        text = (tmp_path / "traces.jsonl").read_text()
        assert "confidential" not in text and "fake-secret" not in text
        assert json.loads(text)["name"] == "parsing"
    finally:
        provider.shutdown()
