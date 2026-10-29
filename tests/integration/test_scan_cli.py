import asyncio
import json

import pytest
import yaml
from sqlalchemy import text
from typer.testing import CliRunner

from ekb.adapters.artifacts import FilesystemArtifactStore
from ekb.adapters.inference import RulesInference
from ekb.adapters.parsers import StructuredParser
from ekb.adapters.persistence.postgres import Postgres
from ekb.adapters.sources import FilesystemConnector
from ekb.config import Settings
from ekb.interfaces.cli import app
from ekb.maintenance import reset_workspace
from ekb.pipeline import IngestionPipeline

pytestmark = pytest.mark.integration


def build(database, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    pipeline = IngestionPipeline(
        database,
        FilesystemArtifactStore(tmp_path / "artifacts"),
        StructuredParser(),
        RulesInference(),
    )
    return pipeline, source


async def test_cli_human_json_replay_and_fresh_use_disposable_state(
    database, tmp_path, monkeypatch
):
    async with database.engine.connect() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
    monkeypatch.setattr("ekb.runtime.Postgres", lambda url: Postgres(url, schema=schema))
    monkeypatch.setenv(
        "EKB_DATABASE_URL", database.engine.url.render_as_string(hide_password=False)
    )
    for name in ("EKB_RECONCILIATION_MODEL_ID", "EKB_EMBEDDING_MODEL_ID"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("EKB_INFERENCE", "rules")
    source = tmp_path / "source"
    source.mkdir()
    (source / "policy.txt").write_text("Users must authenticate.")
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        yaml.safe_dump(
            {
                "data_dir": str(tmp_path / "runtime"),
                "pipeline": {"stages": {"indexing": {"directory": str(source)}}},
            }
        ),
        encoding="utf-8",
    )
    arguments = ["pipeline", "run", "--config", str(profile)]
    first = await asyncio.to_thread(CliRunner().invoke, app, arguments)
    assert first.exit_code == 0, first.output
    assert all(
        value in first.stdout
        for value in ("| ____|", "[1/3]", "[2/3]", "[3/3]", "OVERALL SUMMARY: DONE")
    )
    second = await asyncio.to_thread(CliRunner().invoke, app, [*arguments, "--json"])
    assert second.exit_code == 0, second.output
    report = json.loads(second.stdout)
    assert report["scan"]["known"] == report["scan"]["skipped"] == 1
    assert report["after"]["concepts"] == report["before"]["concepts"] == 1
    fresh = await asyncio.to_thread(
        CliRunner().invoke, app, [*arguments, "--fresh", "--yes", "--json"]
    )
    assert fresh.exit_code == 0, fresh.output
    reset = json.loads(fresh.stdout)
    assert reset["fresh"] and reset["passed"] and reset["before"]["concepts"] == 0
    assert reset["scan"]["unknown"] == reset["scan"]["processed"] == 1
    assert (source / "policy.txt").read_text() == "Users must authenticate."
    (source / "broken.docx").write_bytes(b"Not a Word archive")
    failed = await asyncio.to_thread(CliRunner().invoke, app, [*arguments, "--json"])
    assert failed.exit_code == 1, failed.output
    rejected = json.loads(failed.stdout)
    assert not rejected["passed"] and rejected["run"]["status"] == "failed"
    assert rejected["scan"]["failed"] == 1 and rejected["scan"]["errors"]
    assert rejected["after"]["concepts"] == rejected["before"]["concepts"] == 1


async def test_fresh_preserves_unrelated_tables_inputs_reports_and_migrations(database, tmp_path):
    pipeline, source = build(database, tmp_path)
    (source / "policy.txt").write_text("Users must authenticate.")
    await pipeline.scan([FilesystemConnector(source)])
    data = tmp_path / "data"
    paths = (data / "artifacts", data / "test-corpus/public/microsoft_security_benchmark")
    for path in paths:
        path.mkdir(parents=True)
        (path / "generated.txt").write_text("Generated cache")
    reports = data / "reports"
    reports.mkdir()
    (reports / "retained.json").write_text("{}")
    async with database.engine.begin() as connection:
        await connection.execute(text("CREATE TABLE unrelated (value text)"))
        await connection.execute(text("INSERT INTO unrelated VALUES ('retained')"))
        await connection.execute(text("CREATE TABLE alembic_version (version_num text)"))
        await connection.execute(text("INSERT INTO alembic_version VALUES ('retained')"))
    await reset_workspace(Settings(data_dir=data), database, (source,))
    async with database() as uow:
        assert (await uow.knowledge.counts()).sources == 0
    async with database.engine.connect() as connection:
        for table in ("unrelated", "alembic_version"):
            assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 1
    assert all(not path.exists() for path in paths)
    assert (reports / "retained.json").exists() and (source / "policy.txt").exists()


async def test_fresh_rolls_back_database_and_quarantine_on_filesystem_failure(
    database, tmp_path, monkeypatch
):
    import os

    from ekb.adapters.sources import FilesystemConnector

    pipeline, source = build(database, tmp_path)
    (source / "policy.txt").write_text("Users must authenticate.")
    await pipeline.scan([FilesystemConnector(source)])
    data = tmp_path / "data"
    paths = (data / "artifacts", data / "test-corpus/public/microsoft_security_benchmark")
    for path in paths:
        path.mkdir(parents=True)
        (path / "generated.txt").write_text("Generated cache")
    original = os.replace

    def fail_second(path, destination):
        if path == paths[1]:
            raise PermissionError("Synthetic cache rename failure")
        return original(path, destination)

    monkeypatch.setattr("ekb.maintenance.os.replace", fail_second)
    with pytest.raises(PermissionError):
        await reset_workspace(Settings(data_dir=data), database, (source,))
    async with database() as uow:
        assert (await uow.knowledge.counts()).concepts == 1
    assert all((path / "generated.txt").exists() for path in paths)


async def test_fresh_refuses_an_active_pipeline_lease(database, tmp_path):
    from uuid import uuid4

    async with database() as uow:
        assert await uow.workflow.lease("knowledge-pipeline", uuid4())
        await uow.commit()
    with pytest.raises(ValueError, match="active"):
        await reset_workspace(Settings(data_dir=tmp_path / "runtime"), database)
