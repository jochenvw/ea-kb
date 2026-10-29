from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time
from pathlib import Path

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient

from ekb.domain.models import content_hash


def check_key(key: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("Artifact keys must be SHA-256 digests")


def check_named_key(key: str) -> None:
    if not key or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", part) for part in key.split("/")
    ):
        raise ValueError("Named artifact keys must be safe relative object paths")


def comparable_path(path: Path) -> Path:
    value = str(path)
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return Path(value)


def filesystem_path(path: Path) -> Path:
    value = str(path)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return path
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


class FilesystemArtifactStore:
    def __init__(self, directory: Path, *, document_directory: Path | None = None):
        self.directory = directory.resolve()
        self.document_directory = (document_directory or directory).resolve()

    def _path(self, key: str) -> Path:
        check_key(key)
        path = (self.directory / key[:2] / key).resolve()
        # Windows can add a device prefix while another worker creates the target.
        if not comparable_path(path).is_relative_to(comparable_path(self.directory)):
            raise ValueError("Artifact path escapes its storage root")
        return filesystem_path(path)

    async def put(self, content: bytes) -> str:
        key = content_hash(content)
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if content_hash(path.read_bytes()) != key:
                    raise ValueError("Existing artifact failed its integrity check")
                return
            fd, temporary = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if content_hash(path.read_bytes()) != key:
                        raise ValueError("Concurrent artifact failed its integrity check") from None
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

        await asyncio.to_thread(write)
        return key

    async def get(self, key: str) -> bytes:
        content = await asyncio.to_thread(self._path(key).read_bytes)
        if content_hash(content) != key:
            raise ValueError("Artifact integrity check failed")
        return content

    def _named_path(self, key: str) -> Path:
        check_named_key(key)
        path = self.document_directory.joinpath(*key.split("/")).resolve()
        if not comparable_path(path).is_relative_to(comparable_path(self.document_directory)):
            raise ValueError("Named artifact path escapes its storage root")
        return filesystem_path(path)

    async def put_named(self, key: str, content: bytes) -> None:
        path = self._named_path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Named views/checkpoints can advance; content-addressed originals never change.
                for attempt in range(5):
                    try:
                        os.replace(temporary, path)
                        break
                    except PermissionError:
                        if attempt == 4:
                            raise
                        time.sleep(0.01 * 2**attempt)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

        await asyncio.to_thread(write)

    async def get_named(self, key: str) -> bytes:
        return await asyncio.to_thread(self._named_path(key).read_bytes)


class AzureBlobArtifactStore:
    def __init__(
        self,
        account_url: str,
        container: str,
        *,
        client: BlobServiceClient | None = None,
        document_prefix: str = "test-corpus/public/microsoft_security_benchmark",
    ):
        self.credential = DefaultAzureCredential() if client is None else None
        self.client = client or BlobServiceClient(account_url, credential=self.credential)
        self.container = container
        check_named_key(document_prefix)
        self.document_prefix = document_prefix

    async def put(self, content: bytes) -> str:
        key = content_hash(content)
        blob = self.client.get_blob_client(self.container, key)
        try:
            await blob.upload_blob(content, overwrite=False)
        except ResourceExistsError:
            pass
        return key

    async def get(self, key: str) -> bytes:
        check_key(key)
        stream = await self.client.get_blob_client(self.container, key).download_blob()
        content = await stream.readall()
        if content_hash(content) != key:
            raise ValueError("Azure artifact integrity check failed")
        return content

    async def put_named(self, key: str, content: bytes) -> None:
        check_named_key(key)
        blob = self.client.get_blob_client(self.container, f"{self.document_prefix}/{key}")
        await blob.upload_blob(content, overwrite=True, metadata={"sha256": content_hash(content)})

    async def get_named(self, key: str) -> bytes:
        check_named_key(key)
        try:
            stream = await self.client.get_blob_client(
                self.container, f"{self.document_prefix}/{key}"
            ).download_blob()
        except ResourceNotFoundError as error:
            raise FileNotFoundError(key) from error
        return await stream.readall()

    async def close(self) -> None:
        await self.client.close()
        if self.credential:
            await self.credential.close()
