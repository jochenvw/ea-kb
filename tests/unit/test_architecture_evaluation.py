from pathlib import Path

from ekb.evaluation.architectures import evaluate_architectures


async def test_architecture_assessment_eval_covers_exceptions_and_missing(tmp_path):
    result = await evaluate_architectures(
        Path("evals") / "architecture-assessment-v1.json",
        tmp_path / "architecture-assessment.json",
    )
    assert result["passed"]
    assert len(result["cases"]) == 5
    assert not result["failures"]
