import os
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from ekb.config import Settings
from ekb.interfaces.cli import app
from ekb.interfaces.cli.presentation import ScanConsole, locations
from ekb.maintenance import reset_paths


def test_fresh_requires_confirmation_before_runtime_or_database_access(tmp_path, monkeypatch):
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"data_dir: {tmp_path.as_posix()}/runtime\n", encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("Refusing fresh must not instantiate a runtime")

    monkeypatch.setattr("ekb.interfaces.cli.workflow.Runtime", forbidden)
    declined = CliRunner().invoke(
        app, ["pipeline", "run", "--config", str(profile), "--fresh"], input="n\n"
    )
    assert declined.exit_code != 0 and "Aborted" in declined.output
    for arguments in (
        ["--fresh", "--stages", "issues"],
        ["--fresh", "--json"],
    ):
        result = CliRunner().invoke(app, ["pipeline", "run", *arguments])
        assert result.exit_code == 2 and "requires" in result.output


def test_reset_requires_confirmation_without_starting_runtime_or_reindexing(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Declining reset must not open persistence or start ingestion")

    monkeypatch.setattr("ekb.interfaces.cli.Runtime", forbidden)
    declined = CliRunner().invoke(app, ["reset"], input="n\n")
    assert declined.exit_code != 0 and "Aborted" in declined.output


def test_tui_has_no_ingestion_source_options():
    result = CliRunner().invoke(app, ["tui", "--help"])
    assert result.exit_code == 0
    assert "--directory" not in result.output and "--manifest" not in result.output
    run = CliRunner().invoke(app, ["pipeline", "run", "--help"])
    assert run.exit_code == 0
    assert "--directory" in run.output and "--manifest" in run.output


def test_destructive_schema_recreation_is_not_allowed_at_or_after_v1(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A released application must not recreate its database")

    monkeypatch.setattr("ekb.interfaces.cli.__version__", "1.0.0")
    monkeypatch.setattr("ekb.interfaces.cli.Runtime", forbidden)
    result = CliRunner().invoke(app, ["init", "--recreate"])
    assert result.exit_code == 2 and "before application v1" in result.output


def test_profile_argument_selects_yaml_without_mutating_process_environment(tmp_path, monkeypatch):
    selected, other = tmp_path / "selected.yaml", tmp_path / "other.yaml"
    selected.write_text("scan_concurrency: 7\n", encoding="utf-8")
    other.write_text("scan_concurrency: 9\n", encoding="utf-8")
    monkeypatch.setenv("EKB_CONFIG_FILE", str(other))
    monkeypatch.delenv("EKB_SCAN_CONCURRENCY", raising=False)
    assert Settings(config_file=selected).scan_concurrency == 7
    assert Settings().scan_concurrency == 9
    monkeypatch.setenv("EKB_SCAN_CONCURRENCY", "3")
    assert Settings(config_file=selected).scan_concurrency == 3


def test_foundry_profile_requires_explicit_https_endpoint(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("EKB_"):
            monkeypatch.delenv(name)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    with pytest.raises(ValueError, match="HTTPS Foundry"):
        Settings(config_file=Path("config/foundry.yaml"))
    invalid = CliRunner().invoke(app, ["pipeline", "run", "--config", "config/foundry.yaml"])
    assert invalid.exit_code == 2 and "HTTPS Foundry" in invalid.output
    assert "input_value" not in invalid.output and "ValidationError" not in invalid.output
    monkeypatch.setenv("EKB_MODEL_ENDPOINT", "https://example.openai.azure.com/openai/v1")
    settings = Settings(config_file=Path("config/foundry.yaml"))
    assert settings.model_managed_identity and settings.inference == "agent-framework"
    assert settings.artifact_backend == "filesystem"


def test_reset_rejects_broad_paths_input_overlap_and_junctions(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="dedicated"):
        reset_paths(Settings(data_dir=Path.cwd()))
    settings = Settings(data_dir=tmp_path / "runtime")
    artifact = settings.data_dir / "artifacts"
    with pytest.raises(ValueError, match="source inputs"):
        reset_paths(settings, (artifact / "input.md",))
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path == artifact or original(path))
    with pytest.raises(ValueError, match="junctions"):
        reset_paths(settings)
    with pytest.raises(ValueError, match="shared Azure"):
        reset_paths(Settings(artifact_backend="azure"))


def test_terminal_output_and_links_do_not_leak_credentials_or_control_sequences(capsys, tmp_path):
    from ekb.domain.models import SourceItem, stable_id

    console = ScanConsole()
    console.source(
        SourceItem(
            id=stable_id("unsafe-terminal-title"),
            connector="synthetic",
            name="Policy\x1b[2J\nforged-status",
            location="https://example.invalid",
            format="md",
        ),
        "known",
    )
    printed = capsys.readouterr().out
    assert "\x1b" not in printed and printed.count("\n") == 1
    links = locations(
        Settings(
            data_dir=tmp_path,
            database_url=SecretStr(
                "postgresql+asyncpg://test-user:test-password@localhost:5432/ekb?token=test-token"
            ),
        ),
        tmp_path / "report.json",
    )
    assert not any(secret in str(links) for secret in ("test-user", "test-password", "test-token"))
    assert links["database"] == "postgresql://localhost:5432/ekb"
    assert "pipeline" not in links


def test_terminal_reports_skipped_sources(capsys):
    from ekb.domain.models import SourceItem, stable_id

    console = ScanConsole()
    console.source(
        SourceItem(
            id=stable_id("unchanged"),
            connector="synthetic",
            name="Policy",
            location="https://example.invalid",
            format="md",
        ),
        "skipped",
    )
    assert "[skipped" in capsys.readouterr().out
