import json
from pathlib import Path

from ekb.adapters.inference import RulesInference
from ekb.domain.models import Candidate, ConceptKind, Extraction, Modality
from ekb.evaluation import Case, evaluate, score


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
