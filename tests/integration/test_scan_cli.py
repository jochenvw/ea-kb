import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import uvicorn
import yaml
from docx import Document
from sqlalchemy import text
from sqlalchemy.engine import make_url
from typer.testing import CliRunner

from ekb.adapters.artifacts import FilesystemArtifactStore
from ekb.adapters.inference import RulesInference
from ekb.adapters.parsers import StructuredParser
from ekb.adapters.persistence.postgres import Postgres
from ekb.adapters.sources import FilesystemConnector
from ekb.config import Settings
from ekb.interfaces.api import create_app
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
    monkeypatch.setenv("EKB_CONFIG_FILE", str(profile))
    monkeypatch.setenv("EKB_DATA_DIR", str(tmp_path / "runtime"))
    arguments = ["pipeline", "run", "--config", str(profile)]
    first = await asyncio.to_thread(CliRunner().invoke, app, arguments)
    assert first.exit_code == 0, first.output
    assert all(
        value in first.stdout
        for value in (
            "| ____|",
            "Run ID:",
            "[1/3]",
            "[2/3]",
            "[3/3]",
            "Check indexing/source_ingestion:",
            "OVERALL SUMMARY: DONE",
        )
    )
    report_file = tmp_path / "replay.json"
    second = await asyncio.to_thread(
        CliRunner().invoke, app, [*arguments, "--json", "--output", str(report_file)]
    )
    assert second.exit_code == 0, second.output
    report = json.loads(second.stdout)
    assert json.loads(report_file.read_text(encoding="utf-8")) == report
    assert all("/pipeline" not in link for link in report["links"].values())
    assert report["scan"]["known"] == report["scan"]["skipped"] == 1
    assert report["after"]["concepts"] == report["before"]["concepts"] == 1
    usage = await asyncio.to_thread(CliRunner().invoke, app, ["scans", "usage"])
    assert usage.exit_code == 0, usage.output
    assert json.loads(usage.stdout)["configured_cost"] is None
    scan = await asyncio.to_thread(CliRunner().invoke, app, ["scan", str(source)])
    assert scan.exit_code == 0, scan.output
    assert json.loads(scan.stdout)["skipped"] == 1
    worker = await asyncio.to_thread(
        CliRunner().invoke, app, ["worker", "--directory", str(source), "--once"]
    )
    assert worker.exit_code == 0, worker.output
    scans = await asyncio.to_thread(CliRunner().invoke, app, ["scans", "list"])
    assert scans.exit_code == 0 and json.loads(scans.stdout)[0]["skipped"] == 1
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
    status = await asyncio.to_thread(
        CliRunner().invoke, app, ["pipeline", "status", rejected["run"]["id"]]
    )
    assert status.exit_code == 0, status.output
    persisted = json.loads(status.stdout)
    assert persisted["run"]["status"] == "failed"
    assert any(
        item["kind"] == "check" and item["status"] == "failed" for item in persisted["executions"]
    )
    tasks = await asyncio.to_thread(CliRunner().invoke, app, ["scans", "tasks", "--limit", "10"])
    assert tasks.exit_code == 0, tasks.output
    assert any(task["status"] == "failed" and task["error"] for task in json.loads(tasks.stdout))
    page = await asyncio.to_thread(CliRunner().invoke, app, ["scans", "tasks", "--limit", "1"])
    assert page.exit_code == 0 and len(json.loads(page.stdout)) == 1
    after = json.loads(page.stdout)[0]["id"]
    following = await asyncio.to_thread(
        CliRunner().invoke, app, ["scans", "tasks", "--limit", "1", "--after", after]
    )
    assert following.exit_code == 0 and len(json.loads(following.stdout)) == 1
    assert after < json.loads(following.stdout)[0]["id"]


async def test_real_cli_status_fencing_web_shutdown_replay_and_resume(
    database, tmp_path, monkeypatch
):
    async with database.engine.connect() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
    url = make_url(os.environ["EKB_TEST_DATABASE_URL"]).set(
        query={"dsn": f"postgresql:///?search_path={schema}"}
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "policy.txt").write_text("Users must authenticate.", encoding="utf-8")
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
    for key, value in {
        "EKB_DATABASE_URL": url.render_as_string(hide_password=False),
        "EKB_CONFIG_FILE": str(profile),
        "EKB_DATA_DIR": str(tmp_path / "runtime"),
        "EKB_SOURCE_DIRECTORY": str(source),
        "EKB_INFERENCE": "rules",
        "EKB_ARTIFACT_BACKEND": "filesystem",
        "EKB_RECONCILIATION_MODEL_ID": "",
        "EKB_EMBEDDING_MODEL_ID": "",
    }.items():
        monkeypatch.setenv(key, value)
    env = os.environ.copy()
    executable = Path(sys.executable).parent / ("ekb.exe" if os.name == "nt" else "ekb")
    report_file = tmp_path / "run.json"
    output_file = tmp_path / "run.log"

    def cli(*arguments):
        return subprocess.run(
            [str(executable), *arguments],
            env=env,
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    process = None
    async with database.engine.connect() as blocked:
        transaction = await blocked.begin()
        await blocked.execute(text("LOCK TABLE processing_tasks IN ACCESS EXCLUSIVE MODE"))
        try:
            with output_file.open("w", encoding="utf-8") as output:
                process = await asyncio.create_subprocess_exec(
                    str(executable),
                    "pipeline",
                    "run",
                    "--output",
                    str(report_file),
                    env=env,
                    cwd=Path.cwd(),
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
                run_id = None
                for _ in range(600):
                    if process.returncode is not None:
                        break
                    async with database() as uow:
                        runs = await uow.workflow.executions(limit=1)
                        stages = await uow.workflow.executions(runs[0].id) if runs else []
                    if runs and any(stage.status == "running" for stage in stages):
                        run_id = runs[0].id
                        break
                    await asyncio.sleep(0.1)
                assert run_id is not None, output_file.read_text(encoding="utf-8")

                status = await asyncio.to_thread(cli, "pipeline", "status", str(run_id))
                assert status.returncode == 0, status.stderr
                snapshot = json.loads(status.stdout)
                assert snapshot["run"]["id"] == str(run_id)
                assert snapshot["run"]["status"] == "running"
                assert any(
                    item["kind"] == "stage" and item["id"] for item in snapshot["executions"]
                )

                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                port = listener.getsockname()[1]
                server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error"))
                web = asyncio.create_task(server.serve(sockets=[listener]))
                try:
                    for _ in range(300):
                        if server.started or web.done():
                            break
                        await asyncio.sleep(0.1)
                    assert server.started
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                        assert (await client.get("/health")).status_code == 200
                        assert (await client.get("/")).status_code == 200
                        assert (await client.post("/scans")).status_code == 404
                finally:
                    server.should_exit = True
                    await asyncio.wait_for(web, timeout=30)
                    listener.close()
                assert process.returncode is None

                competing = await asyncio.to_thread(
                    cli, "pipeline", "run", "--json", "--output", str(tmp_path / "competing.json")
                )
                assert competing.returncode != 0
                assert (
                    "owns the renewable execution lease"
                    in json.loads(competing.stdout)["error_detail"]
                )
                assert process.returncode is None
                await transaction.rollback()
                assert await asyncio.wait_for(process.wait(), 120) == 0
        finally:
            if transaction.is_active:
                await transaction.rollback()
            if process and process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), 30)

    human = output_file.read_text(encoding="utf-8")
    assert all(
        part in human
        for part in (
            "Run ID:",
            "[processed",
            "Check indexing/source_ingestion:",
            "OVERALL SUMMARY: DONE",
        )
    )
    first = json.loads(report_file.read_text(encoding="utf-8"))
    assert first["passed"] and first["scan"]["processed"] == 1
    assert all("/pipeline" not in link for link in first["links"].values())

    replay = await asyncio.to_thread(cli, "pipeline", "run", "--json")
    assert replay.returncode == 0, replay.stderr
    repeated = json.loads(replay.stdout)
    assert repeated["scan"]["skipped"] == 1 and repeated["scan"]["processed"] == 0

    broken = source / "broken.docx"
    broken.write_bytes(b"Not a Word archive")
    failure = await asyncio.to_thread(cli, "pipeline", "run", "--json")
    assert failure.returncode != 0
    failed = json.loads(failure.stdout)
    assert failed["run"]["status"] == "failed"
    assert failed["scan"]["failed"] == 1 and failed["scan"]["errors"]
    scans = await asyncio.to_thread(cli, "scans", "list")
    assert scans.returncode == 0 and json.loads(scans.stdout)[0]["errors"]
    tasks = await asyncio.to_thread(cli, "scans", "tasks")
    assert tasks.returncode == 0
    assert any(task["status"] == "failed" and task["error"] for task in json.loads(tasks.stdout))

    document = Document()
    document.add_paragraph("Administrators must review audit logs.")
    document.save(broken)
    resume = await asyncio.to_thread(cli, "pipeline", "resume", failed["run"]["id"])
    assert resume.returncode == 0, resume.stderr
    resumed = json.loads(resume.stdout)
    assert resumed["run"]["id"] == failed["run"]["id"]
    assert resumed["run"]["status"] == "done" and resumed["run"]["attempts"] == 2
    scans = await asyncio.to_thread(cli, "scans", "list")
    latest = json.loads(scans.stdout)[0]
    assert latest["processed"] == latest["skipped"] == 1 and latest["failed"] == 0
    usage = await asyncio.to_thread(cli, "scans", "usage")
    assert usage.returncode == 0 and json.loads(usage.stdout)["configured_cost"] is None


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
