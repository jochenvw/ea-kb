import json
from pathlib import Path

import pytest

from ekb.adapters.inference import RulesInference
from ekb.domain.models import Candidate, ConceptKind, Extraction, Modality
from ekb.evaluation import Case, evaluate, score
from ekb.evaluation.benchmarks import evaluate_benchmarks


def test_false_mandates_and_lost_negation_are_penalized():
    cases = [
        Case.model_validate(
            {
                "id": "test",
                "source": "synthetic",
                "text": "Operators should not disable alarms.",
                "expected": [
                    {
                        "kind": "requirement",
                        "excerpt": "Operators should not disable alarms.",
                        "modality": "recommended",
                    }
                ],
            }
        )
    ]
    prediction = Extraction(
        candidates=[
            Candidate(
                kind=ConceptKind.REQUIREMENT,
                statement="Disable alarms.",
                excerpt=cases[0].text,
                modality=Modality.MANDATED,
            )
        ]
    )
    metrics = score(cases, [prediction])
    assert metrics["requirement"]["tp"] == 0
    assert metrics["requirement"]["fp"] == 1
    assert metrics["requirement"]["fn"] == 1
    assert metrics["false_mandatory_requirements"] == 1
    assert metrics["weighted_error"] == 7


async def test_offline_evaluation_is_reproducible_and_reports_known_limits(tmp_path):
    output = tmp_path / "report.json"
    report = await evaluate(Path("tests/golden/dataset.json"), output, RulesInference())
    assert output.exists() and output.with_suffix(".txt").exists()
    stored = json.loads(output.read_text())
    assert stored["metrics"] == report["metrics"]
    assert report["offline_baseline"] is True
    assert report["metrics"]["evidence_grounding"] == 1
    assert report["metrics"]["false_mandatory_requirements"] == 0
    assert report["metrics"]["known_scope_accuracy"] == 0
    assert report["metrics"]["objective"]["recall"] < 1
    assert report["metrics"]["duplicate_precision"] == 1


async def test_actual_benchmark_excerpts_keep_identifiers_meaning_and_lineage_separate(tmp_path):
    dataset = Path("tests") / "golden" / "benchmarks.json"
    report = await evaluate_benchmarks(dataset, tmp_path / "benchmarks.json")
    extraction = report["metrics"]["extraction"]
    assert extraction["controls"]["precision"] == extraction["controls"]["recall"] == 1
    assert extraction["mappings"]["precision"] == extraction["mappings"]["recall"] == 1
    assert extraction["principle_accuracy"] == extraction["alternatives_accuracy"] == 1
    assert extraction["publication_accuracy"] == extraction["policy_accuracy"] == 1
    assert report["metrics"]["lineage"]["exact_passage_grounding"] == 1
    assert report["metrics"]["lineage"]["published_succession"]["recall"] == 1
    assert report["metrics"]["reconciliation"]["outcome_accuracy"] == 1
    judgments = [r["result"]["judgment"] for r in report["reconciliation"]]
    assert judgments[0]["outcome"] == "more_specific_guidance"
    assert judgments[1]["outcome"] == "distinct"  # Actual ASB reuses NS-6 for different guidance.
    assert report["offline_baseline"] and report["model_calls"] == 0


async def test_benchmark_evaluation_scores_missing_labels_and_rejects_tampered_excerpts(tmp_path):
    data = json.loads((Path("tests") / "golden" / "benchmarks.json").read_bytes())
    data["cases"][0]["expected_control"]["control_id"] = "missing-control"
    dataset = tmp_path / "modified-labels.json"
    dataset.write_text(json.dumps(data), encoding="utf-8")
    report = await evaluate_benchmarks(dataset, tmp_path / "report.json")
    assert report["metrics"]["extraction"]["controls"]["fn"] == 1
    assert report["metrics"]["reconciliation"]["outcome_accuracy"] < 1
    data["cases"][0]["source_excerpt"] += "Invented source content."
    dataset.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity failure"):
        await evaluate_benchmarks(dataset, tmp_path / "refused.json")
