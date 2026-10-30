import json
import threading
from pathlib import Path

from ekb.adapters.inference import RulesInference
from ekb.domain.models import Candidate, Extraction, Modality, SemanticType
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
                        "semantic_type": "TechnicalRequirement",
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
                semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
                statement="Disable alarms.",
                excerpt=cases[0].text,
                modality=Modality.MANDATED,
            )
        ]
    )
    metrics = score(cases, [prediction])
    assert metrics["TechnicalRequirement"]["tp"] == 0
    assert metrics["TechnicalRequirement"]["fp"] == 1
    assert metrics["TechnicalRequirement"]["fn"] == 1
    assert metrics["false_mandatory_requirements"] == 1
    assert metrics["weighted_error"] == 7


def test_unclassified_gold_does_not_invent_or_measure_a_known_ontology_label():
    text = "Operators should retain audit logs."
    case = Case.model_validate(
        {
            "id": "unknown-layer",
            "source": "synthetic",
            "text": text,
            "expected": [{"excerpt": text, "modality": "recommended"}],
        }
    )
    prediction = Extraction(
        candidates=[
            Candidate(
                semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
                statement=text,
                excerpt=text,
                modality=Modality.RECOMMENDED,
            )
        ]
    )
    metrics = score([case], [prediction])
    assert metrics["unclassified"]["tp"] == 1
    assert metrics["TechnicalRequirement"]["precision"] is None


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
    assert report["metrics"]["StrategicGoal"]["recall"] < 1
    assert report["metrics"]["duplicate_precision"] == 1


async def test_evaluation_creates_output_directory_off_event_loop(tmp_path, monkeypatch):
    output = tmp_path / "reports" / "report.json"
    loop_thread = threading.get_ident()
    mkdir_threads = []
    original_mkdir = Path.mkdir

    def tracked_mkdir(path, *args, **kwargs):
        if path == output.parent:
            mkdir_threads.append(threading.get_ident())
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", tracked_mkdir)
    await evaluate(Path("tests/golden/dataset.json"), output, RulesInference())
    assert output.exists()
    assert mkdir_threads and all(thread != loop_thread for thread in mkdir_threads)
