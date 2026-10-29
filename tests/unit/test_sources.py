import asyncio
from pathlib import Path

import httpx
import pytest

from ekb.adapters.sources import FilesystemConnector, HTTPConnector, SourceMissingError
from ekb.domain.models import SourceRecord, SourceSpec
from ekb.services.retry import retry


def specification():
    return SourceSpec(
        id="test",
        publisher="Test",
        name="Test",
        location="https://example.test/doc.txt",
        format="txt",
        category="test",
        access_notes="Synthetic",
        intended_scope="Testing only",
    )


async def test_http_conditional_requests():
    requests = []

    def handle(request):
        requests.append(request)
        if request.headers.get("if-none-match") == '"revision-1"':
            return httpx.Response(304)
        return httpx.Response(
            200, content=b"Users must authenticate.", headers={"etag": '"revision-1"'}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        connector = HTTPConnector([specification()], client=client)
        item = anext(connector.discover())
        item = await item
        first = await connector.fetch(item, None)
        from uuid import uuid4

        previous = SourceRecord(item=item, revision_id=uuid4(), etag=first.etag)
        second = await connector.fetch(item, previous)
        assert second.not_modified and second.content is None
        assert len(requests) == 2
        assert requests[1].headers["if-none-match"] == '"revision-1"'


@pytest.mark.parametrize("status", [404, 410])
async def test_http_missing_source(status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status))
    ) as client:
        connector = HTTPConnector([specification()], client=client)
        item = await anext(connector.discover())
        with pytest.raises(SourceMissingError):
            await connector.fetch(item, None)


async def test_download_size_limit():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 100))
    ) as client:
        connector = HTTPConnector([specification()], client=client, max_bytes=10)
        with pytest.raises(ValueError, match="size limit"):
            await connector.fetch(await anext(connector.discover()), None)


async def test_transient_http_retry_is_bounded():
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            response = httpx.Response(503, request=httpx.Request("GET", "https://example.test"))
            response.raise_for_status()
        return "ok"

    assert await retry(operation, base_delay=0) == "ok" and calls == 3


async def test_filesystem_stability_and_metadata_changes(tmp_path):
    path: Path = tmp_path / "policy.txt"
    await asyncio.to_thread(path.write_text, "Users must authenticate.")
    connector = FilesystemConnector(tmp_path)
    first = await anext(connector.discover())
    assert first.id == (await anext(connector.discover())).id
    await asyncio.to_thread(path.write_text, "Users must retain logs.")
    second = await anext(connector.discover())
    assert second.id == first.id and second.indicator != first.indicator
    assert (await connector.fetch(second, None)).content == b"Users must retain logs."


async def test_missing_directory_is_discovery_failure_not_mass_deletion(tmp_path):
    with pytest.raises(SourceMissingError):
        await anext(FilesystemConnector(tmp_path / "missing").discover())


async def test_json_content_check_and_redirect_landing_page_rejection():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b'{"catalog":{}}')
        )
    ) as client:
        connector = HTTPConnector(
            [specification().model_copy(update={"format": "oscal"})], client=client
        )
        item = await anext(connector.discover())
        assert (await connector.fetch(item, None)).content == b'{"catalog":{}}'
        with pytest.raises(ValueError, match="Expected PDF"):
            await connector.fetch(item.model_copy(update={"format": "pdf"}), None)


async def test_conditional_headers_are_not_reused_for_a_different_source_location():
    from uuid import uuid4

    def handle(request):
        assert "if-none-match" not in request.headers
        return httpx.Response(200, content=b"source")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        connector = HTTPConnector([specification()], client=client)
        item = await anext(connector.discover())
        old = item.model_copy(update={"location": "https://example.test/old"})
        previous = SourceRecord(item=old, revision_id=uuid4(), etag='"old-etag"')
        assert (await connector.fetch(item, previous)).content == b"source"
