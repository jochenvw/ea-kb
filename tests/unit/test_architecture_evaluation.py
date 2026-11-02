from pathlib import Path

from ekb.evaluation.architectures import evaluate_architectures


async def test_architecture_assessment_eval_qualifies_verified_assessment(tmp_path):
    output = tmp_path / "architecture-assessment.json"
    result = await evaluate_architectures(Path("evals") / "architecture-assessment-v2.json", output)

    assert result["passed"], result["failures"]
    assert output.exists()
    assert len(result["families"]) >= 4
    metrics = result["metrics"]
    assert metrics["false_passes"] == 0
    assert metrics["false_violations"] == 0
    assert metrics["omissions_detected"] == metrics["omissions_expected"] > 0
    assert metrics["exceptions_correct"] == metrics["exceptions_expected"] > 0
    assert metrics["unresolved_reported"] == metrics["unresolved_expected"] > 0
