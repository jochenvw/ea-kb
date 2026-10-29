from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from uuid import uuid4

from ekb.adapters.persistence.postgres import Postgres
from ekb.config import Settings


def reset_paths(settings: Settings, protected: tuple[Path, ...] = ()) -> tuple[Path, ...]:
    if settings.artifact_backend != "filesystem":
        raise ValueError("--fresh supports local filesystem artifacts only, not shared Azure blobs")
    data = settings.data_dir.absolute()
    resolved = data.resolve()
    if resolved in {
        Path.home().resolve(),
        Path(resolved.anchor),
    } or Path.cwd().resolve().is_relative_to(resolved):
        raise ValueError("Unsafe data directory: use a dedicated EKB data subfolder")
    paths = (data / "artifacts", data / "test-corpus" / "public" / "microsoft_security_benchmark")
    for path in paths:
        for parent in (path, *path.parents):
            if parent.is_symlink() or parent.is_junction():
                raise ValueError("Reset paths must not traverse symlinks or junctions")
            if parent == data:
                break
        if not path.resolve().is_relative_to(resolved):
            raise ValueError("Cache path escapes the configured data directory")
        if path.exists() and not path.is_dir():
            raise ValueError("Expected a generated cache directory")
        if any(
            source.resolve().is_relative_to(path.resolve())
            or (source.is_dir() and path.resolve().is_relative_to(source.resolve()))
            for source in protected
        ):
            raise ValueError("Reset would delete configured source inputs or configuration")
    return tuple(path.resolve() for path in paths)


async def reset_workspace(
    settings: Settings, database: Postgres, protected: tuple[Path, ...] = ()
) -> None:
    paths = reset_paths(settings, protected)
    moved: list[tuple[Path, Path]] = []
    try:
        async with database.reset():
            for path in paths:
                if await asyncio.to_thread(path.exists):
                    quarantine = path.with_name(f"{path.name}.reset-{uuid4().hex}")
                    await asyncio.to_thread(os.replace, path, quarantine)
                    moved.append((path, quarantine))
    except BaseException:
        for path, quarantine in reversed(moved):
            await asyncio.to_thread(os.replace, quarantine, path)
        raise
    for _, quarantine in moved:
        await asyncio.to_thread(shutil.rmtree, quarantine)
