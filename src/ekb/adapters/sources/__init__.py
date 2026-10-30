from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml
from pydantic import TypeAdapter

from ekb.domain.models import FetchResult, SourceItem, SourceRecord, SourceSpec, stable_id
from ekb.ports import SourceConnector

FORMATS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".json", ".html"}


class SourceMissingError(FileNotFoundError):
    pass


class FilesystemConnector:
    def __init__(self, root: Path, max_bytes: int = 50_000_000):
        self.root = root.resolve()
        self.name = f"filesystem:{stable_id(self.root.as_uri())}"
        self.max_bytes = max_bytes

    async def discover(self) -> AsyncIterator[SourceItem]:
        def walk() -> Iterator[SourceItem]:
            if not self.root.is_dir():
                raise SourceMissingError(f"Source directory is unavailable: {self.root.name}")

            # Path.walk raises traversal failures instead of silently reporting them as deletions.
            def on_error(error: OSError) -> None:
                raise error

            for directory, _, filenames in self.root.walk(on_error=on_error, follow_symlinks=False):
                for filename in sorted(filenames):
                    path = directory / filename
                    if path.is_symlink():
                        continue
                    stat = path.stat()
                    yield SourceItem(
                        id=stable_id(path.as_uri()),
                        connector=self.name,
                        location=path.as_uri(),
                        name=path.name,
                        format=path.suffix[1:].casefold(),
                        indicator=f"{stat.st_mtime_ns}:{stat.st_size}:{stat.st_ctime_ns}",
                        metadata={
                            "path": str(path),
                            "size": stat.st_size,
                            **(
                                {
                                    "metadata_only": True,
                                    "unsupported_format": True,
                                }
                                if path.suffix.casefold() not in FORMATS
                                else {}
                            ),
                        },
                    )

        iterator = walk()
        while (item := await asyncio.to_thread(next, iterator, None)) is not None:
            yield item

    async def fetch(self, item: SourceItem, previous: SourceRecord | None) -> FetchResult:
        path = Path(item.metadata["path"])

        def read() -> bytes:
            before = path.stat()
            if before.st_size > self.max_bytes:
                raise ValueError("Document exceeds configured size limit")
            with path.open("rb") as stream:
                content = stream.read(self.max_bytes + 1)
            after = path.stat()
            indicator = f"{after.st_mtime_ns}:{after.st_size}:{after.st_ctime_ns}"
            if len(content) > self.max_bytes:
                raise ValueError("Document exceeds configured size limit")
            if indicator != item.indicator or before.st_mtime_ns != after.st_mtime_ns:
                raise OSError("Document changed during reading; retry on the next scan")
            return content

        try:
            return FetchResult(content=await asyncio.to_thread(read))
        except FileNotFoundError as error:
            raise SourceMissingError("Source disappeared during the scan") from error


class HTTPConnector:
    def __init__(
        self,
        sources: list[SourceSpec],
        max_bytes: int = 50_000_000,
        *,
        client: httpx.AsyncClient | None = None,
        name: str = "http:manifest",
    ):
        self.sources = sources
        self.max_bytes = max_bytes
        self.name = name
        self.owned_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(90, connect=15),
            follow_redirects=True,
            headers={"User-Agent": "EnterpriseKnowledgeBase/0.1 (public document retrieval)"},
        )

    async def discover(self) -> AsyncIterator[SourceItem]:
        for source in self.sources:
            if not source.enabled:
                continue
            if urlparse(source.location).scheme not in {"https", "http"}:
                raise ValueError("HTTP sources require an HTTP(S) URL")
            yield SourceItem(
                id=stable_id("manifest", source.id),
                connector=self.name,
                location=source.location,
                name=source.name,
                format=source.format,
                metadata=source.model_dump(mode="json"),
            )

    async def fetch(self, item: SourceItem, previous: SourceRecord | None) -> FetchResult:
        headers = {}
        if previous and previous.revision_id and previous.item.location == item.location:
            if previous.etag:
                headers["If-None-Match"] = previous.etag
            if previous.last_modified:
                headers["If-Modified-Since"] = previous.last_modified
        async with self.client.stream("GET", item.location, headers=headers) as response:
            if response.status_code == 304:
                if previous is None or previous.revision_id is None:
                    raise ValueError("Server returned 304 without a known source revision")
                return FetchResult(
                    content=None,
                    etag=response.headers.get("etag", previous.etag),
                    last_modified=response.headers.get("last-modified", previous.last_modified),
                    not_modified=True,
                )
            if response.status_code in {404, 410}:
                raise SourceMissingError(f"HTTP source unavailable: {response.status_code}")
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(f"Expected a complete HTTP document, got {response.status_code}")
            size = response.headers.get("content-length")
            if size and int(size) > self.max_bytes:
                raise ValueError("HTTP document exceeds configured size limit")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > self.max_bytes:
                    raise ValueError("HTTP document exceeds configured size limit")
            raw_content = bytes(content)
            if not raw_content:
                raise ValueError("HTTP source returned an empty document")
            if item.format == "pdf" and not raw_content.startswith(b"%PDF-"):
                raise ValueError(
                    "Expected PDF content, not a redirect landing page or access challenge"
                )
            if item.format in {"json", "oscal", "web-json"} and raw_content.lstrip()[:1] not in {
                b"{",
                b"[",
            }:
                raise ValueError("Expected JSON content")
            if item.format == "xml" and "html" in response.headers.get("content-type", ""):
                raise ValueError("Expected XML content, not an HTML access challenge")
            return FetchResult(
                content=raw_content,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )

    async def close(self) -> None:
        if self.owned_client:
            await self.client.aclose()


def read_manifest(path: Path) -> list[SourceSpec]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return TypeAdapter(list[SourceSpec]).validate_python(raw["sources"])


def connector_plugins() -> dict[str, Any]:
    return {entry.name: entry.load() for entry in entry_points(group="ekb.connectors")}


def manifest_connector(path: Path, max_bytes: int) -> HTTPConnector:
    return HTTPConnector(
        [
            source
            for source in read_manifest(path)
            if source.retrieval != "microsoft-security-benchmark"
        ],
        max_bytes,
        name=f"http:{stable_id(str(path.resolve()))}",
    )


def load_plugin(name: str, configuration: dict[str, Any]) -> SourceConnector:
    plugins = connector_plugins()
    if name not in plugins:
        raise ValueError(f"Unknown connector plugin: {json.dumps(name)}")
    return plugins[name](**configuration)
