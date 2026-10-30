from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ekb.domain.models import content_hash

HISTORY = Path("data") / "evaluation-history"
SKILL_DATASETS = (
    Path("evals") / "skills-v1.json",
    Path("evals") / "documents-v1.json",
)


def _write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        file.write(json.dumps(value, indent=2))


async def persist_run(
    report: dict[str, Any],
    *,
    history: Path = HISTORY,
    prefix: str = "skill-eval",
) -> Path:
    path = history / f"{prefix}-{report['run_id']}.json"
    await asyncio.to_thread(_write_once, path, report)
    if prefix == "skill-eval":
        index = {
            key: report.get(key)
            for key in (
                "run_id",
                "recorded_at",
                "status",
                "dataset_path",
                "dataset_version",
                "dataset_sha256",
                "split",
                "model_id",
                "live",
                "errors",
                "passed",
            )
        }
        index["skills"] = {
            name: {
                key: value.get(key)
                for key in ("metrics", "quality_gates", "performance", "qualified", "errors")
            }
            for name, value in report.get("skills", {}).items()
        }
        await asyncio.to_thread(
            _write_once, history / f"skill-index-{report['run_id']}.json", index
        )
    return path


def _history_reports(history: Path) -> list[dict[str, Any]]:
    reports = []
    if not history.exists():
        return reports
    for path in sorted(history.glob("skill-index-*.json")):
        try:
            reports.append(json.loads(path.read_bytes()))
        except (OSError, json.JSONDecodeError):
            reports.append({"status": "unreadable", "run_id": path.stem})
    return reports


def _report_skills(report: dict[str, Any]) -> dict[str, Any]:
    if isinstance(report.get("skills"), dict):
        return report["skills"]
    if report.get("skill") and isinstance(report.get("metrics"), dict):
        return {report["skill"]: report}
    return {}


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
        except (OSError, json.JSONDecodeError):
            continue
        skills = ("labeling", "extraction")
        for skill in skills:
            matches = [
                report
                for report in reports
                if report.get("dataset_sha256") == content_hash(raw)
                and skill in _report_skills(report)
            ]
            latest = (
                max(matches, key=lambda report: report.get("recorded_at", "")) if matches else None
            )
            catalog.append(
                {
                    "skill": skill,
                    "dataset": str(dataset_path),
                    "dataset_version": metadata.get("version", "unknown"),
                    "dataset_sha256": content_hash(raw),
                    "review_status": metadata.get("review_status", "unknown"),
                    "status": latest.get("status", "never_run") if latest else "never_run",
                    "latest_run_id": latest.get("run_id") if latest else None,
                    "latest_split": latest.get("split") if latest else None,
                    "latest_metrics": (
                        latest["skills"][skill].get("metrics")
                        if latest and skill in latest.get("skills", {})
                        else None
                    ),
                    "latest_performance": (
                        latest["skills"][skill].get("performance")
                        if latest and skill in latest.get("skills", {})
                        else None
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
        if wanted_dataset and report.get("dataset_path") != wanted_dataset:
            continue
        for name, result in _report_skills(report).items():
            if skill and name != skill:
                continue
            rows.append(
                {
                    "run_id": report.get("run_id"),
                    "recorded_at": report.get("recorded_at"),
                    "status": report.get("status", "unknown"),
                    "dataset": report.get("dataset_path"),
                    "dataset_version": report.get("dataset_version"),
                    "split": report.get("split"),
                    "skill": name,
                    "model_id": report.get("model_id"),
                    "metrics": result.get("metrics"),
                    "quality_gates": result.get("quality_gates"),
                    "performance": result.get("performance"),
                    "errors": result.get("errors", 0),
                    "report_path": str(history / f"skill-eval-{report.get('run_id')}.json"),
                }
            )
    return rows
