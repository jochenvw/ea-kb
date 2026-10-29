#!/usr/bin/env python3
"""Download a public EKB ingestion corpus into data/test-corpus/public/.

From repository root:
  uv run python scripts/download_test_corpus.py
  uv run python scripts/download_test_corpus.py --force
  uv run python scripts/download_test_corpus.py --dry-run

Reads config/test_corpus.yaml; keeps assets and a metadata index out of git.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "test_corpus.yaml"
OUT = ROOT / "data" / "test-corpus" / "public"
INDEX = OUT / "index.json"
MAX_BYTES = 110 * 1024 * 1024
ALLOWED_FORMATS = {"pdf", "docx", "pptx", "json", "xml", "html", "txt", "md"}


def safe_slug(raw: str) -> str:
    value = re.sub(r"[^a-z0-9_-]+", "-", raw.lower()).strip("-")
    if not value or value != raw.lower():
        raise ValueError(f"Unsafe ID/category: {raw!r}")
    return value


def validate_payload(path: Path, fmt: str) -> None:
    with path.open("rb") as f:
        start = f.read(8192).lstrip()
    if not start:
        raise ValueError("empty response is not a corpus document")
    if fmt == "pdf" and not start.startswith(b"%PDF-"):
        raise ValueError("expected PDF but content does not have PDF signature")
    if fmt in ("docx", "pptx") and not start.startswith(b"PK"):
        raise ValueError("expected OOXML ZIP but content does not have ZIP signature")
    if fmt == "json":
        with path.open("r", encoding="utf-8") as f:
            json.load(f)
    if fmt == "html" and b"<html" not in start.lower() and b"<!doctype html" not in start.lower():
        raise ValueError("expected an HTML document")


def read_index() -> dict:
    if INDEX.exists():
        return json.loads(INDEX.read_text(encoding="utf-8"))
    return {"schema_version": 1, "items": {}}


def write_index(index: dict) -> None:
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    temp = INDEX.with_suffix(".tmp")
    temp.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, INDEX)


async def download(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    source: dict,
    previous: dict | None,
    *,
    force: bool,
    dry_run: bool,
) -> dict:
    sid = safe_slug(source["id"])
    category = safe_slug(source["category"])
    fmt = source["format"].lower()
    if fmt not in ALLOWED_FORMATS:
        raise ValueError(f"Invalid format {fmt!r} for {sid}")
    # ISO public overview is useful metadata, NOT the licensed full text.
    dest = OUT / category / f"{sid}.{fmt}"
    if dry_run:
        return {
            "status": "would_fetch" if force or not dest.exists() else "would_check",
            "path": str(dest.relative_to(ROOT)),
        }

    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {}
    cached = False
    if dest.is_file() and previous and previous.get("url") == source["url"]:
        try:
            validate_payload(dest, fmt)
            with dest.open("rb") as content:
                cached = hashlib.file_digest(content, "sha256").hexdigest() == previous.get(
                    "sha256"
                )
        except (OSError, ValueError):
            pass
    if not force and cached and previous:
        if previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        elif previous.get("last_modified"):
            headers["If-Modified-Since"] = previous["last_modified"]
        else:
            # No server revision support; skip locally, use --force to refresh.
            return {**previous, "status": "cached", "path": str(dest.relative_to(ROOT))}

    async with semaphore:
        temp = dest.with_name(dest.name + ".part")
        try:
            async with client.stream("GET", source["url"], headers=headers) as response:
                if response.status_code == 304 and cached:
                    return {
                        **(previous or {}),
                        "status": "unchanged",
                        "path": str(dest.relative_to(ROOT)),
                    }
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError(
                        f"Expected complete HTTP 200 document, got {response.status_code}"
                    )
                ctype = response.headers.get("content-type", "").lower()
                if fmt not in ("html",) and "text/html" in ctype:
                    raise ValueError(
                        f"Expected {fmt} but server returned {ctype}; check source URL"
                    )
                sha = hashlib.sha256()
                size = 0
                with temp.open("wb") as out:
                    async for chunk in response.aiter_bytes(chunk_size=256 * 1024):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError("Exceeded 110 MiB download limit")
                        sha.update(chunk)
                        out.write(chunk)
                validate_payload(temp, fmt)
                os.replace(temp, dest)
                return {
                    "status": "downloaded",
                    "path": str(dest.relative_to(ROOT)),
                    "url": source["url"],
                    "sha256": sha.hexdigest(),
                    "bytes": size,
                    "etag": response.headers.get("etag"),
                    "last_modified": response.headers.get("last-modified"),
                    "content_type": ctype,
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "metadata_only": source["category"] == "licensed_standard_metadata",
                }
        except Exception as exc:
            return {
                "status": "failed",
                "url": source["url"],
                "error": str(exc),
                "existing_file_preserved": dest.is_file(),
            }
        finally:
            temp.unlink(missing_ok=True)


async def run(args: argparse.Namespace) -> int:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    if args.benchmark:
        benchmark = manifest["benchmark"]
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "provider": benchmark["provider"],
                        "versions": benchmark["versions"],
                        "network": False,
                        "writes": False,
                    },
                    indent=2,
                )
            )
            return 0
        from ekb.config import Settings
        from ekb.runtime import Runtime

        async with Runtime(Settings(scan_concurrency=args.concurrency)) as runtime:
            report = await runtime.benchmarks().sync(
                benchmark["versions"], args.domains.split(",") if args.domains else None
            )
        print(report.model_dump_json(indent=2))
        return int(report.failed > 0)
    items = manifest["sources"]
    index = read_index()
    old = index.get("items", {})
    sem = asyncio.Semaphore(args.concurrency)
    timeout = httpx.Timeout(90.0, connect=15.0)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        limits=httpx.Limits(max_connections=args.concurrency),
        headers={"User-Agent": "EKB-Ingestion-Test-Corpus/0.1", "Accept": "*/*"},
    ) as client:
        results = await asyncio.gather(
            *(
                download(
                    client, sem, src, old.get(src["id"]), force=args.force, dry_run=args.dry_run
                )
                for src in items
            )
        )
    failed = 0
    failures = {}
    for src, result in zip(items, results, strict=True):
        result["title"] = src["title"]
        result["source_category"] = src["category"]
        result["source_id"] = src["id"]
        label = result["status"].upper()
        print(f"{label:15} {src['id']:30} {result.get('path', result.get('error', ''))}")
        if result["status"] == "failed":
            failed += 1
            failures[src["id"]] = result
            if not args.dry_run and not result["existing_file_preserved"]:
                old.pop(src["id"], None)
        elif not args.dry_run:
            old[src["id"]] = result
    if not args.dry_run:
        index["items"] = old
        index["failures"] = failures
        index["updated_at"] = datetime.now(UTC).isoformat()
        write_index(index)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="redownload even if cached")
    parser.add_argument(
        "--dry-run", action="store_true", help="show destination paths without network access"
    )
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Use the application's resumable Microsoft benchmark downloader instead",
    )
    parser.add_argument("--domains", help="Benchmark domain IDs, comma separated, e.g. NS")
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 16:
        parser.error("--concurrency must be between 1 and 16")
    if args.domains and not args.benchmark:
        parser.error("--domains requires --benchmark")
    if args.force and args.benchmark:
        parser.error(
            "--force applies to the legacy corpus; benchmark sync checks revisions each run"
        )
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
