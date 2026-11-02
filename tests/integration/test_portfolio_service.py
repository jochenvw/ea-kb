"""Portfolio exception risk and change impact over registered reference-app solutions."""

from datetime import date, timedelta

import httpx
import pytest
from test_assessment_service import seed_requirement
from test_reference_app import assess_case, load
from test_reference_app import reference as reference_fixture  # noqa: F401  # pyright: ignore[reportUnusedImport]

from ekb.domain.baselines import BaselineApproval
from ekb.domain.models import SemanticType
from ekb.domain.portfolio import ChangeImpactInput, ExceptionRiskInput
from ekb.evaluation.architectures import RequirementSpec, build_policy
from ekb.interfaces.api import create_app

pytestmark = pytest.mark.integration


async def register_all(runtime, approval, versions):
    cases = load("cases.json")["cases"]
    for case in cases:
        await assess_case(runtime, approval, versions, case, date.fromisoformat("2026-06-30"))
    return cases


async def test_exception_risk_counts_against_applicable_population(reference_fixture):  # noqa: F811
    runtime, approval, versions, _ = reference_fixture
    cases = await register_all(runtime, approval, versions)
    today = date.today()
    request = ExceptionRiskInput(
        baseline_concept_id=approval.concept_id,
        baseline_version_id=approval.version_id,
        as_of=today,
        previous_as_of=today - timedelta(days=400),
    )
    report = await runtime.portfolio.exception_risk(request)
    assert report.solutions == len(cases)
    logging = next(
        item
        for item in report.requirements
        if item.requirement.concept_id == versions["activity-logging"][0]
    )
    # Only the in-date, in-scope exception for the pinned version counts toward exposure.
    assert logging.applicable == len(cases)
    assert logging.active_exceptions == 1
    assert logging.rate == pytest.approx(1 / len(cases), abs=1e-4)
    statuses = sorted(item.status for item in report.exceptions)
    assert statuses == ["active", "expired", "out_of_scope", "wrong_version"]
    assert report.distinct_exception_decisions == 1
    assert report.previous_as_of == request.previous_as_of
    assert any(item.population_changed for item in report.trend)

    app = create_app(runtime)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/portfolio/exception-risk", json=request.model_dump(mode="json")
            )
            assert response.status_code == 200, response.text
            assert response.json()["distinct_exception_decisions"] == 1
            invalid = request.model_copy(update={"previous_as_of": today})
            response = await client.post(
                "/api/portfolio/exception-risk", json=invalid.model_dump(mode="json")
            )
            assert response.status_code == 422


async def test_change_impact_traces_added_and_removed_requirements(reference_fixture):  # noqa: F811
    runtime, approval, versions, catalog = reference_fixture
    cases = await register_all(runtime, approval, versions)
    effective = date(2026, 1, 1)
    specs = [RequirementSpec.model_validate(item) for item in catalog["requirements"]]
    added = specs[0].model_copy(
        update={"key": "added-control", "statement": "Synthetic baseline: an added control."}
    )
    concept = await seed_requirement(runtime.database, added.statement, effective_at=effective)
    changed = {key: value for key, value in versions.items() if key != "authenticated-access"}
    changed["added-control"] = (concept.id, concept.versions[0].id)
    kept = [spec for spec in specs if spec.key != "authenticated-access"]
    obligation = f"{catalog['obligation']} Revised."
    policy = build_policy(catalog["policy"]["id"], "2", obligation, [*kept, added], changed)
    raw = policy.model_dump_json()
    root = await seed_requirement(
        runtime.database, obligation, raw, SemanticType.STRATEGIC_REQUIREMENT, effective
    )
    await runtime.artifacts.put(raw.encode())
    new = BaselineApproval(concept_id=root.id, version_id=root.versions[0].id)
    request = ChangeImpactInput(
        old_concept_id=approval.concept_id,
        old_version_id=approval.version_id,
        new_concept_id=new.concept_id,
        new_version_id=new.version_id,
        stage="proposed",
    )
    report = await runtime.portfolio.change_impact(request)
    kinds = {item.concept_id: item.kind for item in report.changes}
    assert kinds[concept.id] == "added"
    assert kinds[versions["authenticated-access"][0]] == "removed"
    added_items = [item for item in report.items if item.change.concept_id == concept.id]
    assert len(added_items) == len(cases)
    # A proposal never produces confirmed impact.
    assert {item.certainty for item in report.items} <= {"potential", "unresolved"}
    assert report.stage == "proposed_adoption"
    again = await runtime.portfolio.change_impact(request)
    assert [item.id for item in again.items] == [item.id for item in report.items]
    assert report.drafts and sum(len(draft.items) for draft in report.drafts) == len(report.items)

    approved = request.model_copy(update={"stage": "approved", "effective_from": effective})
    report = await runtime.portfolio.change_impact(approved)
    assert report.stage == "approved_effective"
    assert {item.certainty for item in report.items if item.change.concept_id == concept.id} == {
        "confirmed"
    }
