import argparse
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def downloader(tmp_path, monkeypatch):
    script = Path(__file__).parents[2] / "scripts" / "download_test_corpus.py"
    specification = importlib.util.spec_from_file_location("corpus_downloader", script)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "OUT", tmp_path / "corpus")
    monkeypatch.setattr(module, "INDEX", tmp_path / "corpus" / "index.json")
    monkeypatch.setattr(module, "MANIFEST", tmp_path / "manifest.yaml")
    return module


def source():
    return {
        "id": "public-overview",
        "title": "Synthetic public overview",
        "category": "licensed_standard_metadata",
        "format": "html",
        "url": "https://example.test/overview",
    }


@pytest.mark.parametrize("status", [200, 202])
async def test_empty_or_incomplete_download_preserves_good_file(downloader, status):
    destination = downloader.OUT / "licensed_standard_metadata" / "public-overview.html"
    destination.parent.mkdir(parents=True)
    original = b"<html><body>Public overview only.</body></html>"
    destination.write_bytes(original)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, content=b""))
    ) as client:
        result = await downloader.download(
            client, asyncio.Semaphore(1), source(), None, force=True, dry_run=False
        )
    assert result["status"] == "failed"
    assert result["existing_file_preserved"]
    assert destination.read_bytes() == original
    assert not destination.with_suffix(".html.part").exists()


async def test_corrupt_cache_is_refetched_before_conditional_reuse(downloader):
    destination = downloader.OUT / "licensed_standard_metadata" / "public-overview.html"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"")
    content = b"<html><body>Public overview only.</body></html>"
    previous = {
        "url": source()["url"],
        "sha256": hashlib.sha256(content).hexdigest(),
        "etag": '"v1"',
    }
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            assert "if-none-match" not in request.headers
            return httpx.Response(200, content=content, headers={"etag": '"v1"'})
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        first = await downloader.download(
            client, asyncio.Semaphore(1), source(), previous, force=False, dry_run=False
        )
        second = await downloader.download(
            client, asyncio.Semaphore(1), source(), first, force=False, dry_run=False
        )
    assert first["status"] == "downloaded"
    assert first["metadata_only"]
    assert second["status"] == "unchanged"
    assert destination.read_bytes() == content


async def test_failed_sources_are_recorded_in_index_without_fake_downloads(downloader, monkeypatch):
    downloader.MANIFEST.write_text(
        downloader.yaml.safe_dump({"sources": [source()]}), encoding="utf-8"
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(403)))
    monkeypatch.setattr(downloader.httpx, "AsyncClient", lambda **kwargs: client)
    status = await downloader.run(
        argparse.Namespace(
            concurrency=1,
            force=False,
            dry_run=False,
        )
    )
    index = json.loads(downloader.INDEX.read_text(encoding="utf-8"))
    assert status == 1
    assert index["items"] == {}
    assert index["failures"]["public-overview"]["status"] == "failed"
    assert "403" in index["failures"]["public-overview"]["error"]


async def test_dry_run_does_not_download(downloader, monkeypatch, capsys):
    downloader.MANIFEST.write_text(
        downloader.yaml.safe_dump({"sources": [source()]}), encoding="utf-8"
    )

    def forbidden(request):
        raise AssertionError("Dry-run must not make a network request")

    client = httpx.AsyncClient(transport=httpx.MockTransport(forbidden))
    monkeypatch.setattr(downloader.httpx, "AsyncClient", lambda **kwargs: client)
    status = await downloader.run(
        argparse.Namespace(
            concurrency=1,
            force=False,
            dry_run=True,
        )
    )
    assert status == 0 and "WOULD_FETCH" in capsys.readouterr().out
    assert not downloader.OUT.exists()
