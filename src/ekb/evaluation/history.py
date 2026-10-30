from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, NotRequired, TypedDict
from uuid import uuid4

from pydantic import TypeAdapter

from ekb.domain.models import content_hash

HISTORY = Path("data") / "evaluation-history"
SKILL_DATASETS = (
    Path("evals") / "skills-v1.json",
    Path("evals") / "documents-v1.json",
)


class SkillSummary(TypedDict):
    metrics: dict[str, int | float | None]
    quality_gates: dict[str, bool]
    performance: dict[str, int | float | bool | None]
    qualified: bool
    errors: int
    safety_gates: NotRequired[dict[str, bool]]
    deployment: NotRequired[str]
    actual_model_ids: NotRequired[list[str]]
    prompt_versions: NotRequired[list[str]]
    fingerprint: NotRequired[str]
    evaluator_fingerprint: NotRequired[str]


class SkillIndex(TypedDict):
    run_id: str
    recorded_at: str
    status: str
    dataset_path: str
    dataset_version: str
    dataset_sha256: str
    split: str
    model_id: str
    live: bool
    errors: int
    passed: bool
    skills: dict[str, SkillSummary]
    provider: NotRequired[str | None]
    model_version: NotRequired[str | None]
    gates_sha256: NotRequired[str]


INDEX = TypeAdapter(SkillIndex)


def _write_staged(path: Path, value: object, publish: Callable[[Path, Path], None]) -> None:
    payload = json.dumps(value, indent=2, allow_nan=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".pending-{uuid4().hex}")
    created = False
    try:
        with staging.open("xb") as file:
            created = True
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        publish(staging, path)
    finally:
        if created:
            staging.unlink(missing_ok=True)


def _write_once(path: Path, value: object) -> None:
    _write_staged(path, value, os.link)


async def write_latest_report(path: Path, value: object) -> None:
    await asyncio.to_thread(_write_staged, path, value, os.replace)


def validate_output_path(output: Path) -> None:
    for path in (output, output.resolve()):
        if (
            path.name.casefold().startswith(("skill-eval-", "skill-index-", "skill-comparison-"))
            and path.suffix.casefold() == ".json"
        ):
            raise ValueError("A latest-report output cannot overwrite immutable evaluation history")


async def persist_run(
    report: dict[str, Any],
    *,
    history: Path = HISTORY,
    prefix: str = "skill-eval",
) -> Path:
    if prefix not in {"skill-eval", "skill-comparison"} or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]*", report["run_id"]
    ):
        raise ValueError("Invalid evaluation report identity")
    index = INDEX.validate_python(report) if prefix == "skill-eval" else None
    path = history / f"{prefix}-{report['run_id']}.json"
    await asyncio.to_thread(_write_once, path, report)
    if index is not None:
        await asyncio.to_thread(
            _write_once, history / f"skill-index-{report['run_id']}.json", index
        )
    return path


def _history_reports(history: Path) -> list[SkillIndex]:
    reports: list[SkillIndex] = []
    if not history.exists():
        return reports
    indexed: set[str] = set()
    for path in sorted(history.glob("skill-index-*.json")):
        try:
            report = INDEX.validate_json(path.read_bytes())
            expected_name = f"skill-index-{report['run_id']}.json"
            if path.name != expected_name:
                raise ValueError("Evaluation history index identity does not match its filename")
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read evaluation history index: {path.name}") from error
        if not (history / f"skill-eval-{report['run_id']}.json").is_file():
            raise ValueError(f"Cannot read evaluation history report for: {path.name}")
        reports.append(report)
        indexed.add(report["run_id"])
    for path in sorted(history.glob("skill-eval-*.json")):
        run_id = path.name.removeprefix("skill-eval-").removesuffix(".json")
        if run_id in indexed:
            continue
        try:
            report = INDEX.validate_json(path.read_bytes())
            if report["run_id"] != run_id:
                raise ValueError("Evaluation report identity does not match its filename")
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read evaluation history report: {path.name}") from error
        reports.append(report)
    reports.sort(key=lambda report: report["recorded_at"])
    return reports


def list_skill_catalog(
    datasets: tuple[Path, ...] = SKILL_DATASETS,
    history: Path = HISTORY,
) -> list[dict[str, Any]]:
    reports = _history_reports(history)
    catalog = []
    for dataset_path in datasets:
        try:
            raw = dataset_path.read_bytes()
            metadata = json.loads(raw)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Cannot read evaluation dataset: {dataset_path}") from error
        dataset_hash = content_hash(raw)
        for skill in ("labeling", "extraction"):
            matches = [
                report
                for report in reports
                if report["dataset_sha256"] == dataset_hash and skill in report["skills"]
            ]
            latest = max(matches, key=lambda report: report["recorded_at"]) if matches else None
            catalog.append(
                {
                    "skill": skill,
                    "dataset": str(dataset_path),
                    "dataset_version": metadata.get("version", "unknown"),
                    "dataset_sha256": dataset_hash,
                    "review_status": metadata.get("review_status", "unknown"),
                    "status": latest["status"] if latest else "never_run",
                    "latest_run_id": latest["run_id"] if latest else None,
                    "latest_split": latest["split"] if latest else None,
                    "latest_model_id": latest["model_id"] if latest else None,
                    "latest_deployment": (
                        latest["skills"][skill].get("deployment", latest["model_id"])
                        if latest
                        else None
                    ),
                    "latest_actual_model_ids": (
                        latest["skills"][skill].get("actual_model_ids", []) if latest else None
                    ),
                    "latest_model_version": latest.get("model_version") if latest else None,
                    "latest_provider": latest.get("provider") if latest else None,
                    "latest_metrics": latest["skills"][skill]["metrics"] if latest else None,
                    "latest_quality_gates": (
                        latest["skills"][skill]["quality_gates"] if latest else None
                    ),
                    "latest_performance": (
                        latest["skills"][skill]["performance"] if latest else None
                    ),
                    "latest_qualified": latest["skills"][skill]["qualified"] if latest else None,
                    "latest_passed": latest["passed"] if latest else None,
                    "runs": len(matches),
                }
            )
    return catalog


def skill_history(
    *,
    dataset: Path | None = None,
    skill: str | None = None,
    history: Path = HISTORY,
) -> list[dict[str, Any]]:
    wanted_dataset = str(dataset) if dataset else None
    rows = []
    for report in _history_reports(history):
        if wanted_dataset and report["dataset_path"] != wanted_dataset:
            continue
        for name, result in report["skills"].items():
            if skill and name != skill:
                continue
            rows.append(
                {
                    "run_id": report["run_id"],
                    "recorded_at": report["recorded_at"],
                    "status": report["status"],
                    "dataset": report["dataset_path"],
                    "dataset_version": report["dataset_version"],
                    "dataset_sha256": report["dataset_sha256"],
                    "split": report["split"],
                    "skill": name,
                    "model_id": report["model_id"],
                    "deployment": result.get("deployment", report["model_id"]),
                    "actual_model_ids": result.get("actual_model_ids", []),
                    "model_version": report.get("model_version"),
                    "provider": report.get("provider"),
                    "skill_fingerprint": result.get("fingerprint"),
                    "evaluator_fingerprint": result.get("evaluator_fingerprint"),
                    "metrics": result["metrics"],
                    "quality_gates": result["quality_gates"],
                    "safety_gates": result.get("safety_gates"),
                    "performance": result["performance"],
                    "errors": result["errors"],
                    "report_path": str(history / f"skill-eval-{report['run_id']}.json"),
                }
            )
    return rows
