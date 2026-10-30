from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from ekb.adapters.inference import RulesInference
from ekb.adapters.parsers import StructuredParser
from ekb.config import Settings
from ekb.evaluation.dataset import write_json
from ekb.evaluation.frugal import qualify
from ekb.evaluation.nist import evaluate_nist
from ekb.evaluation.skills import evaluate_skills, skill_run_metadata
from ekb.runtime import Runtime


async def browser_tests(output: Path, frontend: Path = Path("frontend")) -> dict:
    npm = shutil.which("npm")
    if not npm or not (frontend / "package.json").is_file():
        return {"passed": False, "error": "UI checks require Node.js/npm and a source checkout."}
    path = frontend / "test-results" / "results.json"
    await asyncio.to_thread(path.unlink, missing_ok=True)
    process = await asyncio.create_subprocess_exec(
        npm,
        "run",
        "verify",
        cwd=frontend,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    await asyncio.to_thread((output / "frontend.txt").write_bytes, stdout)
    stats = {}
    if await asyncio.to_thread(path.exists):
        report = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
        stats = report.get("stats", {})
    return {
        "exit_code": process.returncode,
        "stats": stats,
        "passed": process.returncode == 0
        and stats.get("expected", 0) > 0
        and not any(stats.get(key, 0) for key in ("unexpected", "skipped", "flaky")),
    }


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
    ui: bool = False,
) -> dict:
    if not unit_only and not os.environ.get("EKB_TEST_DATABASE_URL"):
        raise ValueError("Set EKB_TEST_DATABASE_URL for the full suite, or select --unit-only")
    if live and (
        settings.inference not in {"openai", "agent-framework"}
        or not settings.model_id
        or not settings.reconciliation_model_id
        or not settings.embedding_model_id
    ):
        raise ValueError(
            "Live suite requires explicit student, reconciliation and embedding models"
        )
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    frontend = await browser_tests(output) if ui else None
    tests = await code_tests(output, unit_only)
    evaluation = None
    skills = None
    documents = None
    nist = None
    error = None
    code_passed = tests["passed"] and (frontend is None or frontend["passed"])
    if code_passed:
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
                    skills = await evaluate_skills(
                        Path("evals") / "skills-v1.json",
                        gates,
                        output / "skills.json",
                        runtime.inference,
                        live=True,
                        run_metadata=skill_run_metadata(settings),
                    )
                    documents = await evaluate_skills(
                        Path("evals") / "documents-v1.json",
                        gates,
                        output / "documents.json",
                        runtime.inference,
                        split="all",
                        live=True,
                        run_metadata=skill_run_metadata(settings),
                    )
                    nist = await evaluate_nist(
                        None,
                        output / "nist",
                        runtime.parser,
                        runtime.inference,
                        live=True,
                    )
            else:
                evaluation = await qualify(
                    dataset,
                    gates,
                    output / "evaluation.json",
                    RulesInference(),
                )
                skills = await evaluate_skills(
                    Path("evals") / "skills-v1.json",
                    gates,
                    output / "skills.json",
                    RulesInference(),
                    run_metadata=skill_run_metadata(None),
                )
                documents = await evaluate_skills(
                    Path("evals") / "documents-v1.json",
                    gates,
                    output / "documents.json",
                    RulesInference(),
                    split="all",
                    run_metadata=skill_run_metadata(None),
                )
                nist = await evaluate_nist(
                    None, output / "nist", StructuredParser(), RulesInference()
                )
        except Exception as exc:
            error = type(exc).__name__
    report = {
        "profile": "live" if live else "offline",
        "unit_only": unit_only,
        "passed": code_passed
        and bool(evaluation and evaluation["passed"])
        and bool(skills and skills["passed"])
        and bool(documents and documents["passed"])
        and bool(nist and nist["passed"]),
        "tests": tests,
        "frontend": frontend,
        "evaluation": evaluation,
        "skills": skills,
        "documents": documents,
        "nist": nist,
        "evaluation_error": error,
        "coverage": {
            "indexing_provenance": "pipeline/integration/contract tests + benchmark eval",
            "extraction_modality_scope": "actual-source and synthetic teacher extraction cases",
            "document_classification": "annual report/NIST mixed layers + ISO metadata negative",
            "nist_extraction": "Core taxonomy, class/provenance checks; unrun negatives explicit",
            "semantic_duplicates_lineage": "teacher pairs + benchmark eval + workflow tests",
            "embedding_retrieval": "held-out teacher queries, Recall@k/MRR + provider/cache tests",
            "publication_versions": "benchmark eval + domain/history/scope tests",
            "governance_resolution_issues": "workflow/integration/contract tests",
            "interfaces": "HTTP, MCP and headless TUI integration tests",
            "browser": "shadcn UI checks/build + Playwright regressions" if ui else "not selected",
            "infrastructure": "local Bicep compilation and ARM safeguards (no deployment)",
            "efficiency": "token, priced cost, latency, replay and bounded-work checks",
        },
        "limitations": [
            "Offline success is not live model qualification.",
            "A unit-only profile excludes database and transport integration evidence.",
            *([] if ui else ["Browser regressions require --ui and the frontend toolchain."]),
        ],
    }
    await write_json(output / "suite.json", report)
    return report
