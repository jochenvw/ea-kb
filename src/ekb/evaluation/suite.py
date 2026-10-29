from __future__ import annotations

import asyncio
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from ekb.adapters.inference import RulesInference
from ekb.config import Settings
from ekb.evaluation.dataset import write_json
from ekb.evaluation.frugal import qualify
from ekb.runtime import Runtime


async def code_tests(output: Path, unit_only: bool) -> dict:
    path = output / "tests.xml"
    await asyncio.to_thread(path.unlink, missing_ok=True)
    command = [sys.executable, "-m", "pytest", "-q", f"--junitxml={output / 'tests.xml'}"]
    if unit_only:
        command.extend(["-m", "not integration"])
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("EKB_") or key == "EKB_TEST_DATABASE_URL"
    }
    environment.update(
        EKB_INFERENCE="rules",
        EKB_RECONCILIATION_MODEL_ID="",
        EKB_EMBEDDING_MODEL_ID="",
        EKB_ARTIFACT_BACKEND="filesystem",
        EKB_AZURE_MONITOR_CONNECTION_STRING="",
        EKB_MODEL_ENDPOINT="http://127.0.0.1:1/v1",
        EKB_MODEL_API_KEY="",
        EKB_MODEL_MANAGED_IDENTITY="false",
        EKB_MODEL_TEMPERATURE="0",
        EKB_MODEL_MAX_TOKEN_PARAMETER="max_tokens",
        EKB_CONFIG_FILE=str(Path("config") / "local.yaml"),
        EKB_PIPELINE="{}",
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    await asyncio.to_thread((output / "tests.txt").write_bytes, stdout)
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    if await asyncio.to_thread(path.exists):
        root = await asyncio.to_thread(ET.parse, path)
        for suite in root.getroot().iter("testsuite"):
            for key in summary:
                summary[key] += int(suite.attrib.get(key, "0"))
    return {
        "exit_code": process.returncode,
        **summary,
        "passed": process.returncode == 0
        and summary["tests"] > 0
        and (unit_only or summary["skipped"] == 0),
    }


async def run_suite(
    settings: Settings,
    dataset: Path,
    gates: Path,
    output: Path,
    *,
    live: bool = False,
    unit_only: bool = False,
) -> dict:
    if not unit_only and not os.environ.get("EKB_TEST_DATABASE_URL"):
        raise ValueError("Set EKB_TEST_DATABASE_URL for the full suite, or select --unit-only")
    if live and (
        settings.inference != "openai"
        or not settings.model_id
        or not settings.reconciliation_model_id
        or not settings.embedding_model_id
    ):
        raise ValueError(
            "Live suite requires explicit student, reconciliation and embedding models"
        )
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    tests = await code_tests(output, unit_only)
    evaluation = None
    error = None
    if tests["passed"]:
        try:
            if live:
                async with Runtime(settings) as runtime:
                    evaluation = await qualify(
                        dataset,
                        gates,
                        output / "evaluation.json",
                        runtime.inference,
                        runtime.verifier,
                        runtime.embeddings,
                        live=True,
                        embedding_input_price=settings.embedding_input_price,
                    )
            else:
                evaluation = await qualify(
                    dataset,
                    gates,
                    output / "evaluation.json",
                    RulesInference(),
                )
        except Exception as exc:
            error = type(exc).__name__
    report = {
        "profile": "live" if live else "offline",
        "unit_only": unit_only,
        "passed": tests["passed"] and bool(evaluation and evaluation["passed"]),
        "tests": tests,
        "evaluation": evaluation,
        "evaluation_error": error,
        "coverage": {
            "indexing_provenance": "pipeline/integration/contract tests + benchmark eval",
            "extraction_modality_scope": "actual-source and synthetic teacher extraction cases",
            "semantic_duplicates_lineage": "teacher pairs + benchmark eval + workflow tests",
            "embedding_retrieval": "held-out teacher queries, Recall@k/MRR + provider/cache tests",
            "publication_versions": "benchmark eval + domain/history/scope tests",
            "governance_resolution_issues": "workflow/integration/contract tests",
            "interfaces": "HTTP, MCP and headless TUI integration tests",
            "infrastructure": "local Bicep compilation and ARM safeguards (no deployment)",
            "efficiency": "token, priced cost, latency, replay and bounded-work checks",
        },
        "limitations": [
            "Offline success is not live model qualification.",
            "A unit-only profile excludes database and transport integration evidence.",
        ],
    }
    await write_json(output / "suite.json", report)
    return report
