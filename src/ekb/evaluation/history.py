from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, TypedDict

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


INDEX = TypeAdapter(SkillIndex)


def _write_once(path: Path, value: object) -> None:
    payload = json.dumps(value, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        file.write(payload)


async def persist_run(
    report: dict[str, Any],
    *,
    history: Path = HISTORY,
    prefix: str = "skill-eval",
) -> Path:
    index = INDEX.validate_python(report) if prefix == "skill-eval" else None
    path = history / f"{prefix}-{report['run_id']}.json"
    await asyncio.to_thread(_write_once, path, report)
    if index is not None:
        await asyncio.to_thread(
            _write_once, history / f"skill-index-{report['run_id']}.json", index
        )
    return path


def _history_reports(history: Path) -> list[SkillIndex]:
    reports = []
    if not history.exists():
        return reports
    for path in sorted(history.glob("skill-index-*.json")):
        try:
            reports.append(INDEX.validate_json(path.read_bytes()))
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read evaluation history index: {path.name}") from error
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
                    "latest_metrics": latest["skills"][skill]["metrics"] if latest else None,
                    "latest_performance": (
                        latest["skills"][skill]["performance"] if latest else None
                    ),
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
                    "split": report["split"],
                    "skill": name,
                    "model_id": report["model_id"],
                    "metrics": result["metrics"],
                    "quality_gates": result["quality_gates"],
                    "performance": result["performance"],
                    "errors": result["errors"],
                    "report_path": str(history / f"skill-eval-{report['run_id']}.json"),
                }
            )
    return rows
