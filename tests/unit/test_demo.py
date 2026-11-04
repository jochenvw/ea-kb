import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

from ekb.config import Settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def demo(monkeypatch, tmp_path):
    specification = importlib.util.spec_from_file_location(
        "demo_launcher", ROOT / "scripts" / "demo.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    monkeypatch.setitem(sys.modules, specification.name, module)
    specification.loader.exec_module(module)
    monkeypatch.setattr(module, "DATA", tmp_path / "demo-data")
    return module


def test_bundled_snapshot_contains_pinned_originals_and_populated_dump(demo):
    manifest, files = demo.read_snapshot()
    assert len(manifest.sources) == 2
    assert manifest.counts["concepts"] > 0
    assert manifest.counts["occurrences"] > 0
    assert files["database.dump"].startswith(b"PGDMP")
    for source in manifest.sources:
        assert source.location.startswith("https://raw.githubusercontent.com/usnistgov/")
        key = source.artifact_key
        assert demo.digest(files[f"artifacts/{key[:2]}/{key}"]) == key


def test_snapshot_rejects_stale_schema_and_corrupt_archive(demo, monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "SCHEMA", tmp_path / "models.py")
    demo.SCHEMA.write_text("changed schema", encoding="utf-8")
    with pytest.raises(ValueError, match="schema is stale"):
        demo.read_snapshot()
    monkeypatch.setattr(
        demo, "SCHEMA", ROOT / "src" / "ekb" / "adapters" / "persistence" / "postgres" / "models.py"
    )
    directory = tmp_path / "bundle"
    directory.mkdir()
    (directory / "manifest.json").write_bytes((demo.DEMO / "manifest.json").read_bytes())
    (directory / "snapshot.zip").write_bytes(b"corrupt")
    monkeypatch.setattr(demo, "DEMO", directory)
    with pytest.raises(ValueError, match="archive checksum"):
        demo.read_snapshot()


@pytest.mark.parametrize("kind", ["unexpected-path", "duplicate", "corrupt-original", "too-large"])
def test_snapshot_rejects_unsafe_or_inconsistent_members(demo, monkeypatch, tmp_path, kind):
    manifest, files = demo.read_snapshot()
    directory = tmp_path / "bundle"
    directory.mkdir()
    archive = directory / "snapshot.zip"
    first_original = next(name for name in files if name.startswith("artifacts/"))
    if kind == "unexpected-path":
        files["../escaped"] = b"unexpected"
    if kind == "corrupt-original":
        files[first_original] = b"corrupt"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
        if kind == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                bundle.writestr("database.dump", files["database.dump"])
    manifest.archive_sha256 = demo.digest(archive.read_bytes())
    (directory / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(demo, "DEMO", directory)
    if kind == "too-large":
        monkeypatch.setattr(demo, "MAX_UNPACKED", 1)
    with pytest.raises(ValueError, match="archive entries|original checksum"):
        demo.read_snapshot()
    assert not (tmp_path / "escaped").exists()


def test_restore_refuses_unrecognized_database_before_writing_artifacts(demo, monkeypatch):
    manifest, files = demo.read_snapshot()
    monkeypatch.setattr(demo, "query", lambda sql: "1")
    with pytest.raises(ValueError, match="refusing reset"):
        demo.restore(manifest, files)
    assert not demo.DATA.exists()


def test_restore_is_transactional_and_rerun_preserves_existing_database(demo, monkeypatch):
    manifest, files = demo.read_snapshot()
    commands = []
    table_count = 0

    def query(sql):
        if "pg_tables" in sql:
            return str(table_count)
        table = sql.rsplit(".", 1)[1]
        return str(manifest.counts[table])

    monkeypatch.setattr(demo, "query", query)
    monkeypatch.setattr(demo, "compose", lambda *args: commands.append(args))
    demo.restore(manifest, files)
    assert any(
        "pg_restore" in command and "--single-transaction" in command for command in commands
    )
    assert (demo.DATA / "snapshot.sha256").read_text().strip() == manifest.archive_sha256
    table_count = len(manifest.counts)
    commands.clear()
    demo.restore(manifest, files)
    assert not commands
    for source in manifest.sources:
        key = source.artifact_key
        assert demo.digest((demo.DATA / "artifacts" / key[:2] / key).read_bytes()) == key


def test_restore_refuses_changed_snapshot_or_corrupt_existing_original(demo, monkeypatch):
    manifest, files = demo.read_snapshot()
    demo.DATA.mkdir()
    marker = demo.DATA / "snapshot.sha256"
    marker.write_text("previous snapshot", encoding="ascii")
    monkeypatch.setattr(demo, "query", lambda sql: "1")
    with pytest.raises(ValueError, match="refusing reset"):
        demo.restore(manifest, files)
    marker.write_text(manifest.archive_sha256, encoding="ascii")
    key = manifest.sources[0].artifact_key
    original = demo.DATA / "artifacts" / key[:2] / key
    original.parent.mkdir(parents=True)
    original.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        demo.restore(manifest, files)
    assert original.read_bytes() == b"corrupt"


def test_demo_overrides_paid_providers_in_environment_and_dotenv(demo, monkeypatch, tmp_path):
    monkeypatch.setenv("EKB_INFERENCE", "agent-framework")
    monkeypatch.setenv("EKB_MODEL_API_KEY", "synthetic-env-key")
    monkeypatch.setenv("EKB_CONFIG_FILE", "nonexistent-cloud-profile.yaml")
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "EKB_INFERENCE=openai",
                "EKB_RECONCILIATION_MODEL_ID=paid-model",
                "EKB_EMBEDDING_MODEL_ID=paid-embedding",
                "EKB_ARTIFACT_BACKEND=azure",
                "EKB_MODEL_MANAGED_IDENTITY=true",
                "EKB_AZURE_MONITOR_CONNECTION_STRING=synthetic-connection",
            ]
        ),
        encoding="utf-8",
    )
    for key, value in demo.offline_environment().items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.inference == "rules"
    assert settings.reconciliation_model_id == settings.embedding_model_id == ""
    assert settings.model_api_key.get_secret_value() == ""
    assert settings.azure_monitor_connection_string.get_secret_value() == ""
    assert settings.artifact_backend == "filesystem"
    assert not settings.model_managed_identity
    assert settings.data_dir == demo.DATA
    assert "127.0.0.1:5433/ekb" in settings.database_url.get_secret_value()


def test_manifest_has_no_operational_or_governance_rows():
    manifest = json.loads((ROOT / "demo" / "manifest.json").read_text(encoding="utf-8"))
    for table in (
        "authority_events",
        "reconciliation_decisions",
        "enrichment_findings",
        "processing_tasks",
        "processing_runs",
        "pipeline_executions",
        "pipeline_leases",
        "semantic_replay_cache",
        "solution_assessments",
    ):
        assert manifest["counts"][table] == 0
