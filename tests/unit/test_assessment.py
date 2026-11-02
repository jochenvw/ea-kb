from datetime import date
from uuid import uuid4

import pytest

from ekb.domain.assessment import (
    AssessmentException,
    AssessmentReference,
    AssessmentTarget,
    ImplementationObservation,
    RequirementAssessment,
    assess_solution,
)
from ekb.domain.models import Scope


def target():
    return AssessmentTarget(
        solution_id=uuid4(),
        architecture_revision_id=uuid4(),
        artifact_sha256="a" * 64,
        scope=Scope(application="Example", environment="production"),
        as_of=date(2026, 11, 2),
    )


def requirement(t, **updates):
    item = RequirementAssessment(
        requirement=AssessmentReference(
            concept_id=uuid4(),
            version_id=uuid4(),
            statement="Protect stored records.",
            evidence_location="baseline:records",
        ),
        applicability="applicable",
        applicability_reason="The selected production baseline applies.",
        authoritative=True,
        design="accepted",
        design_reason="The design implements the baseline.",
        observations=(
            ImplementationObservation(
                resource_id="records",
                check_id="encryption",
                check_version="1",
                status="passed",
                detail="Encryption enabled.",
                artifact_sha256=t.artifact_sha256,
                location="template:resources/0",
            ),
        ),
        observation_coverage_complete=True,
    )
    return item.model_copy(update=updates)


def result(t, *items, complete=True):
    return assess_solution(t, items, baseline_revision="baseline-v1", baseline_complete=complete)


def test_assessment_is_fail_closed_for_missing_authority_coverage_and_design():
    t = target()
    assert result(t, requirement(t)).conforms
    assert not result(t).conforms
    assert not result(t, requirement(t), complete=False).acceptable_with_exceptions
    for changes, expected in [
        ({"authoritative": False}, "unknown"),
        ({"applicability": "unknown"}, "unknown"),
        ({"design": "unresolved"}, "decision_required"),
        ({"design": "violated"}, "violated"),
        ({"observations": ()}, "unknown"),
        ({"observation_coverage_complete": False}, "unknown"),
    ]:
        assessment = result(t, requirement(t, **changes))
        assert assessment.findings[0].disposition == expected
        assert not assessment.acceptable_with_exceptions


@pytest.mark.parametrize("check", ["encryption", "logging", "identity", "network"])
def test_checks_preserve_resource_failures_and_evidence_identity(check):
    t = target()
    item = requirement(t)
    passed = item.observations[0].model_copy(update={"check_id": check})
    failed = passed.model_copy(update={"resource_id": "other", "status": "failed"})
    assessment = result(t, item.model_copy(update={"observations": (passed, failed)}))
    assert assessment.findings[0].disposition == "violated"
    assert assessment.coverage.violated == 1
    assert assessment.findings[0].observations[1].resource_id == "other"
    for changes in ({"artifact_sha256": "b" * 64}, {"basis": "deployed"}):
        wrong = passed.model_copy(update=changes)
        assert (
            result(t, item.model_copy(update={"observations": (wrong,)})).findings[0].disposition
            == "unknown"
        )


def test_exception_is_bound_to_target_requirement_scope_and_time():
    t = target()
    item = requirement(t, observations=(), design="violated")
    exception = AssessmentException(
        decision_id=uuid4(),
        solution_id=t.solution_id,
        architecture_revision_id=t.architecture_revision_id,
        requirement_version_id=item.requirement.version_id,
        scope=t.scope,
        rationale="Temporary migration.",
        approved_by="board",
        effective_from=t.as_of,
        effective_until=t.as_of,
    )
    valid = result(t, item.model_copy(update={"exception": exception}))
    assert valid.acceptable_with_exceptions and not valid.conforms
    assert valid.coverage.excepted == 1
    for change in (
        {"effective_from": date(2027, 1, 1)},
        {"effective_until": date(2026, 1, 1)},
        {"solution_id": uuid4()},
        {"architecture_revision_id": uuid4()},
        {"requirement_version_id": uuid4()},
        {"scope": Scope(environment="test")},
        {"revoked": True},
    ):
        invalid = exception.model_copy(update=change)
        assessment = result(t, item.model_copy(update={"exception": invalid}))
        assert not assessment.acceptable_with_exceptions


def test_non_applicability_needs_authority_and_does_not_require_implementation():
    t = target()
    item = requirement(t, applicability="not_applicable", observations=())
    assert result(t, item).conforms
    assert result(t, item).coverage.not_applicable == 1
    assert not result(t, item.model_copy(update={"authoritative": False})).conforms


def test_unsupported_observation_and_changed_inputs_remain_explicit():
    t = target()
    item = requirement(t)
    unsupported = item.observations[0].model_copy(update={"status": "unsupported"})
    assessment = result(t, item.model_copy(update={"observations": (unsupported,)}))
    assert assessment.findings[0].implementation == "unsupported"
    assert assessment.coverage.unresolved == 1
    assert assessment.fingerprint != result(t, item).fingerprint
    assert assessment.findings[0].id == result(t, item).findings[0].id
