import asyncio
import json
import os
import socket
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
import yaml
from docx import Document
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from typer.testing import CliRunner

from ekb.adapters.artifacts import FilesystemArtifactStore
from ekb.adapters.inference import RulesInference
from ekb.adapters.parsers import StructuredParser
from ekb.adapters.persistence.postgres import Postgres
from ekb.adapters.persistence.postgres.models import TaskRow, WorkflowLeaseRow
from ekb.adapters.sources import FilesystemConnector
from ekb.domain.models import now
from ekb.interfaces.api import create_app
from ekb.interfaces.cli import app
from ekb.maintenance import reset_workspace
from ekb.pipeline import IngestionPipeline

pytestmark = pytest.mark.integration

LEGACY_TABLES = (
    "concept_aliases",
    "benchmark_documents",
    "benchmark_comparisons",
    "benchmark_embeddings",
    "benchmark_match_cache",
)


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


async def recovery_cli(database, tmp_path):
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
    env = os.environ.copy()
    env.update(
        {
            "EKB_DATABASE_URL": url.render_as_string(hide_password=False),
            "EKB_CONFIG_FILE": str(profile),
            "EKB_DATA_DIR": str(tmp_path / "runtime"),
            "EKB_SOURCE_DIRECTORY": str(source),
            "EKB_INFERENCE": "rules",
            "EKB_ARTIFACT_BACKEND": "filesystem",
            "EKB_RECONCILIATION_MODEL_ID": "",
            "EKB_EMBEDDING_MODEL_ID": "",
            "EKB_AZURE_MONITOR_CONNECTION_STRING": "",
            "EKB_OCR": "false",
        }
    )
    executable = Path(sys.executable).parent / ("ekb.exe" if os.name == "nt" else "ekb")

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

    return env, cli


async def wait_for_blocked_claim(database, process, output_file):
    observed = None
    for _ in range(900):
        if process.returncode is not None:
            pytest.fail(f"CLI exited before the claim: {output_file.read_text(encoding='utf-8')}")
        async with database() as uow:
            tasks = await uow.processing.tasks()
            scans = await uow.processing.scans()
            runs = await uow.workflow.executions(limit=1)
            waiting = await uow.session.scalar(
                text(
                    "SELECT pid FROM pg_locks "
                    "WHERE relation = to_regclass('parse_checkpoints') AND NOT granted LIMIT 1"
                )
            )
            observed = len(runs), len(scans), len(tasks), waiting
            if tasks and scans and runs and tasks[0].status == "running" and waiting:
                task = await uow.session.get(TaskRow, tasks[0].id)
                assert task is not None and task.owner is not None
                return runs[0].id, scans[0].id, task.id, task.owner, waiting
        await asyncio.sleep(0.1)
    pytest.fail(
        f"CLI did not reach the blocked claim ({observed}): "
        f"{output_file.read_text(encoding='utf-8')}"
    )


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
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE concept_aliases "
                "(id integer PRIMARY KEY, alias_id uuid REFERENCES concepts(id))"
            )
        )
        await connection.execute(
            text("INSERT INTO concept_aliases (id, alias_id) SELECT 1, id FROM concepts LIMIT 1")
        )
        assert await connection.scalar(text("SELECT count(*) FROM concept_aliases")) == 1
    fresh = await asyncio.to_thread(
        CliRunner().invoke, app, [*arguments, "--fresh", "--yes", "--json"]
    )
    assert fresh.exit_code == 0, fresh.output
    reset = json.loads(fresh.stdout)
    assert reset["fresh"] and reset["passed"] and reset["before"]["concepts"] == 0
    assert reset["scan"]["unknown"] == reset["scan"]["processed"] == 1
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT to_regclass('concept_aliases')")) is None
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
    async with database() as uow:
        retained = (await uow.sources.list())[0]
        assert retained.revision_id
        revision = await uow.sources.revision(retained.revision_id)
        assert revision
        artifact_key = revision.artifact_key
    data = tmp_path / "data"
    paths = (
        data / "artifacts",
        data / "test-corpus" / "public" / "retained-source",
        data / "test-corpus" / "public" / "microsoft_security_benchmark",
    )
    for path in paths:
        path.mkdir(parents=True)
        (path / "original.txt").write_text("Retained original")
    reports = data / "reports"
    reports.mkdir()
    (reports / "retained.json").write_text("{}")
    async with database.engine.begin() as connection:
        await connection.execute(text("CREATE TABLE unrelated (value text)"))
        await connection.execute(text("INSERT INTO unrelated VALUES ('retained')"))
        await connection.execute(text("CREATE TABLE alembic_version (version_num text)"))
        await connection.execute(text("INSERT INTO alembic_version VALUES ('retained')"))
    await reset_workspace(database)
    async with database() as uow:
        assert (await uow.knowledge.counts()).sources == 0
    async with database.engine.connect() as connection:
        for table in ("unrelated", "alembic_version"):
            assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 1
    assert all((path / "original.txt").read_text() == "Retained original" for path in paths)
    assert (reports / "retained.json").exists() and (source / "policy.txt").exists()
    assert await pipeline.artifacts.get(artifact_key) == b"Users must authenticate."


async def test_cli_recreate_retains_originals_and_unrelated_resources(
    database, tmp_path, monkeypatch
):
    async with database.engine.connect() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
    monkeypatch.setattr("ekb.runtime.Postgres", lambda url: Postgres(url, schema=schema))
    monkeypatch.setenv(
        "EKB_DATABASE_URL", database.engine.url.render_as_string(hide_password=False)
    )
    data = tmp_path / "runtime"
    monkeypatch.setenv("EKB_DATA_DIR", str(data))
    monkeypatch.setenv("EKB_INFERENCE", "rules")
    for name in ("EKB_RECONCILIATION_MODEL_ID", "EKB_EMBEDDING_MODEL_ID"):
        monkeypatch.setenv(name, "")
    source = tmp_path / "input"
    source.mkdir()
    original = b"Users must authenticate before access.\n"
    (source / "source.txt").write_bytes(original)
    old_corpus = data / "test-corpus" / "public" / "microsoft_security_benchmark"
    old_corpus.mkdir(parents=True)
    (old_corpus / "downloaded-source.txt").write_bytes(original)
    artifacts = FilesystemArtifactStore(data / "artifacts")
    pipeline = IngestionPipeline(database, artifacts, StructuredParser(), RulesInference())
    report = await pipeline.scan([FilesystemConnector(source)])
    assert report.processed == 1
    async with database() as uow:
        source_record = (await uow.sources.list())[0]
        assert source_record.revision_id
        revision = await uow.sources.revision(source_record.revision_id)
        assert revision
        artifact_key = revision.artifact_key
    assert await artifacts.get(artifact_key) == original
    async with database.engine.begin() as connection:
        for name in LEGACY_TABLES:
            await connection.execute(text(f'CREATE TABLE "{name}" (id integer PRIMARY KEY)'))
        await connection.execute(text("CREATE TABLE unrelated_records (value text)"))
        await connection.execute(text("INSERT INTO unrelated_records VALUES ('retained')"))
    result = await asyncio.to_thread(CliRunner().invoke, app, ["init", "--recreate"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["recreated"] is True
    async with database.engine.connect() as connection:
        for name in LEGACY_TABLES:
            assert (
                await connection.scalar(text("SELECT to_regclass(:name)"), {"name": name}) is None
            )
        assert await connection.scalar(text("SELECT count(*) FROM sources")) == 0
        assert await connection.scalar(text("SELECT value FROM unrelated_records")) == "retained"
    assert await artifacts.get(artifact_key) == original
    assert (source / "source.txt").read_bytes() == original
    assert (old_corpus / "downloaded-source.txt").read_bytes() == original


@pytest.mark.parametrize("recreate", [False, True])
@pytest.mark.parametrize("blocked_table", ["concept_aliases", "benchmark_documents"])
async def test_reset_rolls_back_when_unrelated_table_depends_on_legacy_table(
    database, tmp_path, recreate, blocked_table
):
    from sqlalchemy.exc import DBAPIError

    pipeline, source = build(database, tmp_path)
    (source / "policy.txt").write_text("Users must authenticate.")
    await pipeline.scan([FilesystemConnector(source)])
    async with database.engine.begin() as connection:
        for name in LEGACY_TABLES:
            await connection.execute(text(f'CREATE TABLE "{name}" (id integer PRIMARY KEY)'))
        await connection.execute(
            text(f'CREATE TABLE unrelated_dependency (id integer REFERENCES "{blocked_table}")')
        )
        await connection.execute(text(f'INSERT INTO "{blocked_table}" VALUES (17)'))
        await connection.execute(text("INSERT INTO unrelated_dependency VALUES (17)"))
    with pytest.raises(DBAPIError):
        await reset_workspace(database, recreate=recreate)
    async with database() as uow:
        assert (await uow.knowledge.counts()).concepts == 1
    async with database.engine.connect() as connection:
        for name in LEGACY_TABLES:
            assert (
                await connection.scalar(text("SELECT to_regclass(:name)"), {"name": name})
                is not None
            )
        assert await connection.scalar(text(f'SELECT id FROM "{blocked_table}"')) == 17
        assert await connection.scalar(text("SELECT id FROM unrelated_dependency")) == 17
        assert (
            await connection.scalar(text("SELECT to_regclass('unrelated_dependency')")) is not None
        )
    assert (source / "policy.txt").exists()


async def test_fresh_refuses_an_active_pipeline_lease(database):
    from uuid import uuid4

    async with database.engine.begin() as connection:
        for name in LEGACY_TABLES:
            await connection.execute(text(f'CREATE TABLE "{name}" (id integer PRIMARY KEY)'))
    async with database() as uow:
        assert await uow.workflow.lease("knowledge-pipeline", uuid4())
        await uow.commit()
    with pytest.raises(ValueError, match="active"):
        await reset_workspace(database)
    async with database.engine.connect() as connection:
        for name in LEGACY_TABLES:
            assert (
                await connection.scalar(text("SELECT to_regclass(:name)"), {"name": name})
                is not None
            )


async def test_fresh_refuses_a_standalone_scan_before_any_task_is_claimed(database, tmp_path):
    pipeline, source = build(database, tmp_path)
    started, release = asyncio.Event(), asyncio.Event()

    class PausedConnector(FilesystemConnector):
        async def discover(self):
            started.set()
            await release.wait()
            async for item in super().discover():
                yield item

    scan = asyncio.create_task(pipeline.scan([PausedConnector(source)]))
    try:
        await asyncio.wait_for(started.wait(), 10)
        async with database() as uow:
            assert (await uow.processing.scans())[0].status == "running"
            assert not await uow.processing.tasks()
            assert not await uow.session.scalar(select(WorkflowLeaseRow.name).limit(1))
        with pytest.raises(ValueError, match="active"):
            async with database.reset():
                pass
    finally:
        release.set()
        report = await asyncio.wait_for(scan, 30)
    assert report.status == "completed"


async def test_real_cli_cooperative_interrupt_is_diagnosable_and_resumes_same_run(
    database, tmp_path
):
    env, cli = await recovery_cli(database, tmp_path)
    output_file = tmp_path / "interrupted.log"
    trigger = tmp_path / "interrupt.trigger"
    env["EKB_TEST_INTERRUPT_FILE"] = str(trigger)
    # Windows console control events require a shared console; inject SIGINT in the CLI main thread.
    launcher = """
import _thread
import os
import sys
import threading
import time

def interrupt():
    while not os.path.isfile(os.environ["EKB_TEST_INTERRUPT_FILE"]):
        time.sleep(0.05)
    _thread.interrupt_main()
    print("INTERRUPT_QUEUED", file=sys.stderr, flush=True)

threading.Thread(target=interrupt, daemon=True).start()
from ekb.interfaces.cli import app
app()
"""
    process = None
    async with database.engine.connect() as blocked:
        transaction = await blocked.begin()
        await blocked.execute(text("LOCK TABLE parse_checkpoints IN ACCESS EXCLUSIVE MODE"))
        try:
            with output_file.open("w", encoding="utf-8") as output:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    launcher,
                    "pipeline",
                    "run",
                    "--json",
                    env=env,
                    cwd=Path.cwd(),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
                run_id, scan_id, task_id, _, _ = await wait_for_blocked_claim(
                    database, process, output_file
                )
                trigger.write_text("interrupt", encoding="utf-8")
                for _ in range(300):
                    if "INTERRUPT_QUEUED" in output_file.read_text(encoding="utf-8"):
                        break
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail("The CLI did not receive the cooperative interrupt")
                await transaction.rollback()
                assert await asyncio.wait_for(process.wait(), 120) != 0
        finally:
            if transaction.is_active:
                await transaction.rollback()
            if process and process.returncode is None:
                process.kill()
                await asyncio.wait_for(process.wait(), 30)

    status = await asyncio.to_thread(cli, "pipeline", "status", str(run_id))
    assert status.returncode == 0, status.stderr
    cancelled = json.loads(status.stdout)
    assert cancelled["run"]["id"] == str(run_id)
    assert cancelled["run"]["status"] == "cancelled"
    assert cancelled["run"]["error"] == "CancelledError"
    assert any(
        item["kind"] == "check" and item["status"] == "cancelled"
        for item in cancelled["executions"]
    )
    async with database() as uow:
        scan = next(report for report in await uow.processing.scans() if report.id == scan_id)
        task = next(item for item in await uow.processing.tasks() if item.id == task_id)
        assert scan.status == "cancelled" and scan.finished_at is not None
        assert task.status == "cancelled"

    resume = await asyncio.to_thread(cli, "pipeline", "resume", str(run_id))
    assert resume.returncode == 0, resume.stderr
    resumed = json.loads(resume.stdout)
    assert resumed["run"]["id"] == str(run_id)
    assert resumed["run"]["status"] == "done" and resumed["run"]["attempts"] == 2
    async with database() as uow:
        task = next(item for item in await uow.processing.tasks() if item.id == task_id)
        counts = await uow.knowledge.counts()
        assert task.status == "done" and task.attempts == 2
        assert counts.sources == counts.revisions == counts.concepts == 1


async def test_crashed_cli_resume_waits_for_task_lease_and_stale_scan_blocks_reset(
    database, tmp_path
):
    env, cli = await recovery_cli(database, tmp_path)
    executable = Path(sys.executable).parent / ("ekb.exe" if os.name == "nt" else "ekb")
    output_file = tmp_path / "crashed.log"
    process = None
    async with database.engine.connect() as blocked:
        transaction = await blocked.begin()
        await blocked.execute(text("LOCK TABLE parse_checkpoints IN ACCESS EXCLUSIVE MODE"))
        try:
            with output_file.open("w", encoding="utf-8") as output:
                process = await asyncio.create_subprocess_exec(
                    str(executable),
                    "pipeline",
                    "run",
                    "--json",
                    env=env,
                    cwd=Path.cwd(),
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
                run_id, scan_id, task_id, old_owner, backend_pid = await wait_for_blocked_claim(
                    database, process, output_file
                )
                process.kill()
                assert await asyncio.wait_for(process.wait(), 30) != 0
        finally:
            if transaction.is_active:
                await transaction.rollback()
            if process and process.returncode is None:
                process.kill()
                await asyncio.wait_for(process.wait(), 30)

    async with database.engine.connect() as connection:
        for _ in range(300):
            exists = await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid = :pid)"),
                {"pid": backend_pid},
            )
            await connection.rollback()
            if not exists:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("The crashed CLI's blocked PostgreSQL session did not disconnect")

    with pytest.raises(ValueError, match="active"):
        async with database.reset():
            pass
    async with database.engine.begin() as connection:
        await connection.execute(
            update(WorkflowLeaseRow)
            .where(WorkflowLeaseRow.name == "knowledge-pipeline")
            .values(expires_at=now() - timedelta(seconds=1))
        )

    early = await asyncio.to_thread(cli, "pipeline", "resume", str(run_id))
    assert early.returncode != 0, f"Resume incorrectly succeeded: {early.stdout[:600]}"
    status = await asyncio.to_thread(cli, "pipeline", "status", str(run_id))
    assert status.returncode == 0, status.stderr
    failed = json.loads(status.stdout)
    assert failed["run"]["id"] == str(run_id)
    assert failed["run"]["status"] == "failed" and failed["run"]["attempts"] == 2
    scans = await asyncio.to_thread(cli, "scans", "list")
    assert scans.returncode == 0, scans.stderr
    assert any(
        report["status"] == "partial"
        and any("ProcessingIncompleteError" in error for error in report["errors"])
        for report in json.loads(scans.stdout)
    )
    async with database() as uow:
        assert (await uow.knowledge.counts()).concepts == 0

    async with database.engine.begin() as connection:
        await connection.execute(
            update(TaskRow)
            .where(TaskRow.id == task_id)
            .values(lease_until=now() - timedelta(seconds=1))
        )
    resume = await asyncio.to_thread(cli, "pipeline", "resume", str(run_id))
    assert resume.returncode == 0, resume.stderr
    resumed = json.loads(resume.stdout)
    assert resumed["run"]["id"] == str(run_id)
    assert resumed["run"]["status"] == "done" and resumed["run"]["attempts"] == 3
    async with database() as uow:
        task = next(item for item in await uow.processing.tasks() if item.id == task_id)
        reports = await uow.processing.scans()
        assert task.status == "done" and task.attempts == 2
        assert (await uow.knowledge.counts()).concepts == 1
        assert next(report for report in reports if report.id == scan_id).status == "running"
        with pytest.raises(RuntimeError, match="Processing lease lost"):
            await uow.processing.heartbeat(task_id, old_owner)
    async with database.engine.connect() as connection:
        assert not await connection.scalar(
            select(WorkflowLeaseRow.name).where(WorkflowLeaseRow.expires_at > now()).limit(1)
        )
        assert not await connection.scalar(
            select(TaskRow.id)
            .where(TaskRow.status == "running", TaskRow.lease_until > now())
            .limit(1)
        )
    # Unowned historical scan reports cannot safely be distinguished from live standalone scans.
    with pytest.raises(ValueError, match="active"):
        async with database.reset():
            pass
