"""Restore the reviewed snapshot into isolated local storage, then launch the existing web CLI."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
DATA = ROOT / "data" / "demo"
SCHEMA = ROOT / "src" / "ekb" / "adapters" / "persistence" / "postgres" / "models.py"
MAX_UNPACKED = 25_000_000
COMPOSE = ["docker", "compose", "-p", "ekb-demo", "-f", str(DEMO / "compose.yaml")]


class DemoSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    location: str
    artifact_key: str = Field(pattern=r"^[a-f0-9]{64}$")


class DemoManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_version: str
    source_commit: str
    created_at: str
    schema_sha256: str
    archive_sha256: str
    sources: list[DemoSource]
    counts: dict[str, int]


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_snapshot() -> tuple[DemoManifest, dict[str, bytes]]:
    manifest = DemoManifest.model_validate_json((DEMO / "manifest.json").read_bytes())
    if digest(SCHEMA.read_text(encoding="utf-8").encode()) != manifest.schema_sha256:
        raise ValueError("Demo schema is stale; use its matching checkout or refresh the snapshot.")
    archive = DEMO / "snapshot.zip"
    if digest(archive.read_bytes()) != manifest.archive_sha256:
        raise ValueError("Demo archive checksum mismatch.")
    expected = {"database.dump"}
    expected.update(
        f"artifacts/{source.artifact_key[:2]}/{source.artifact_key}" for source in manifest.sources
    )
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if (
            {entry.filename for entry in entries} != expected
            or len(entries) != len(expected)
            or sum(entry.file_size for entry in entries) > MAX_UNPACKED
        ):
            raise ValueError("Unexpected demo archive entries or unpacked size.")
        files = {entry.filename: bundle.read(entry) for entry in entries}
    if not files["database.dump"].startswith(b"PGDMP"):
        raise ValueError("Expected a PostgreSQL custom-format dump.")
    for source in manifest.sources:
        key = source.artifact_key
        if digest(files[f"artifacts/{key[:2]}/{key}"]) != key:
            raise ValueError("Demo original checksum mismatch.")
    return manifest, files


def compose(*arguments: str, capture: bool = False) -> str:
    result = subprocess.run(
        [*COMPOSE, *arguments],
        cwd=ROOT,
        check=True,
        capture_output=capture,
        text=True,
        timeout=180,
    )
    return result.stdout.strip() if capture else ""


def query(sql: str) -> str:
    return compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-X",
        "-U",
        "ekb",
        "-d",
        "ekb",
        "-v",
        "ON_ERROR_STOP=1",
        "-Atc",
        sql,
        capture=True,
    )


def restore(manifest: DemoManifest, files: dict[str, bytes]) -> None:
    marker = DATA / "snapshot.sha256"
    tables = int(query("SELECT count(*) FROM pg_tables WHERE schemaname='public'"))
    if tables and (
        not marker.is_file()
        or marker.read_text(encoding="ascii").strip() != manifest.archive_sha256
    ):
        raise ValueError(
            "Demo database is not empty or belongs to another snapshot; refusing reset."
        )
    DATA.mkdir(parents=True, exist_ok=True)
    for source in manifest.sources:
        key = source.artifact_key
        target = DATA / "artifacts" / key[:2] / key
        if target.exists() and digest(target.read_bytes()) != key:
            raise ValueError(
                f"Existing demo original is corrupt: {target}; refusing to overwrite it."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(files[f"artifacts/{key[:2]}/{key}"])
    if not tables:
        with tempfile.TemporaryDirectory(prefix="ekb-demo-") as directory:
            dump = Path(directory) / "database.dump"
            dump.write_bytes(files["database.dump"])
            compose("cp", str(dump), "postgres:/tmp/ekb-demo.dump")
            try:
                compose(
                    "exec",
                    "-T",
                    "postgres",
                    "pg_restore",
                    "-U",
                    "ekb",
                    "-d",
                    "ekb",
                    "--single-transaction",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-acl",
                    "/tmp/ekb-demo.dump",
                )
                for table, expected in manifest.counts.items():
                    if not re.fullmatch(r"[a-z_]+", table):
                        raise ValueError("Invalid snapshot table name.")
                    if int(query(f"SELECT count(*) FROM public.{table}")) != expected:
                        raise ValueError(f"Restored demo count mismatch: {table}")
            finally:
                compose("exec", "-T", "postgres", "rm", "-f", "/tmp/ekb-demo.dump")
        marker.write_text(manifest.archive_sha256 + "\n", encoding="ascii")
        print("Restored precomputed demo knowledge and exact originals.", flush=True)
    else:
        print("Reusing the demo database; existing edits are preserved.", flush=True)


def offline_environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("EKB_")}
    environment.update(
        EKB_CONFIG_FILE=str(ROOT / "config" / "local.yaml"),
        EKB_DATABASE_URL="postgresql+asyncpg://ekb:ekb@127.0.0.1:5433/ekb",
        EKB_DATA_DIR=str(DATA),
        EKB_ARTIFACT_BACKEND="filesystem",
        EKB_INFERENCE="rules",
        EKB_MODEL_ID="",
        EKB_LABELING_MODEL_ID="",
        EKB_RECONCILIATION_MODEL_ID="",
        EKB_EMBEDDING_MODEL_ID="",
        EKB_MODEL_API_KEY="",
        EKB_MODEL_MANAGED_IDENTITY="false",
        EKB_AZURE_MONITOR_CONNECTION_STRING="",
        EKB_DEBUG_ARTIFACTS="false",
        EKB_GOVERNANCE_TOKEN="",
    )
    return environment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore-only", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        manifest, files = read_snapshot()
        compose("up", "-d", "--wait", "postgres")
        restore(manifest, files)
        if not args.restore_only:
            print(f"Offline demo: http://127.0.0.1:{args.port}", flush=True)
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--frozen",
                    "ekb",
                    "web",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.port),
                ],
                cwd=ROOT,
                env=offline_environment(),
                check=True,
            )
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        parser.exit(1, f"Demo startup failed: {error}\n")


if __name__ == "__main__":
    main()
