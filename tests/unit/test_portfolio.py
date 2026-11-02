from datetime import date
from uuid import UUID, uuid4

from ekb.domain.assessment import (
    AssessmentException,
    AssessmentReference,
    AssessmentTarget,
    RequirementAssessment,
)
from ekb.domain.baselines import (
    BaselinePolicy,
    BaselineRequirement,
    VerificationOption,
    WorkloadContext,
)
from ekb.domain.models import Scope
from ekb.domain.portfolio import (
    SolutionPlan,
    change_impact,
    compare,
    diff_baselines,
    exception_risk,
    exception_status,
)

AS_OF = date(2026, 6, 30)
SCOPE = Scope(application="portfolio", environment="production")


def rule(concept: UUID, version: UUID | None = None, **values) -> BaselineRequirement:
    options = (
        VerificationOption(id="primary", check_id="logging", resource_type="Example/type"),
        VerificationOption(id="alternative", check_id="logging", resource_type="Example/type"),
    )
    return BaselineRequirement(
        concept_id=concept, version_id=version or uuid4(), options=options, **values
    )


def policy(*rules: BaselineRequirement, version: str = "1") -> BaselinePolicy:
    return BaselinePolicy(
        id="baseline", version=version, statement="Synthetic obligation.", requirements=rules
    )


def reference(item: BaselineRequirement) -> AssessmentReference:
    return AssessmentReference(
        concept_id=item.concept_id,
        version_id=item.version_id,
        statement=f"Requirement {item.concept_id}",
        evidence_location="requirements.json#1",
    )


def waiver(
    solution: UUID,
    version: UUID,
    decision: UUID | None = None,
    rationale: str = "Migration in progress.",
    **values,
) -> AssessmentException:
    return AssessmentException(
        decision_id=decision or uuid4(),
        solution_id=solution,
        architecture_revision_id=solution,
        requirement_version_id=version,
        scope=values.pop("scope", SCOPE),
        rationale=rationale,
        approved_by="Synthetic board",
        effective_from=values.pop("effective_from", date(2026, 1, 1)),
        **values,
    )


def plan(
    rules: list[tuple[BaselineRequirement, dict]],
    *,
    solution: UUID | None = None,
    team: str | None = "team-a",
    repository: str | None = "repo-a",
    criticality=None,
    exposure=None,
) -> SolutionPlan:
    solution = solution or uuid4()
    items = tuple(
        RequirementAssessment(
            requirement=reference(item),
            applicability=values.get("applicability", "applicable"),
            applicability_reason="Selected by the baseline.",
            authoritative=values.get("authoritative", True),
            design=values.get("design", "accepted"),
            design_reason="Recorded.",
            decision_location=values.get("location", "design.json#1"),
            option=values.get("option", "primary"),
            exception=(
                waiver(solution, item.version_id, **values["exception"])
                if "exception" in values
                else None
            ),
        )
        for item, values in rules
    )
    return SolutionPlan(
        solution_id=solution,
        revision_id=solution,
        version="1",
        name=f"solution-{solution}",
        design_available=True,
        context=WorkloadContext(criticality=criticality, exposure=exposure),
        target=AssessmentTarget(
            solution_id=solution,
            architecture_revision_id=solution,
            artifact_sha256="0" * 64,
            scope=SCOPE,
            as_of=AS_OF,
            owner="Owner",
            team=team,
            repository=repository,
        ),
        requirements=items,
    )


def test_rates_use_the_applicable_population_not_raw_counts():
    logging = rule(uuid4())
    excepted = [(logging, {"exception": {}})]
    small = [plan(excepted), plan([(logging, {})])]
    large = [
        plan(excepted),
        *(plan([(logging, {})]) for _ in range(7)),
        plan([(logging, {"applicability": "not_applicable"})]),
        plan([(logging, {"authoritative": False})]),
    ]
    first = exception_risk(policy(logging), "a", small, AS_OF).requirements[0]
    second = exception_risk(policy(logging), "a", large, AS_OF).requirements[0]
    assert (first.active_exceptions, first.applicable, first.rate) == (1, 2, 0.5)
    assert (second.active_exceptions, second.applicable, second.rate) == (1, 8, 0.125)
    assert (second.not_applicable, second.applicability_unknown) == (1, 1)
    trend = compare(
        exception_risk(policy(logging), "a", large, AS_OF),
        exception_risk(policy(logging), "a", small, date(2026, 1, 1)),
    ).trend[0]
    assert trend.population_changed and trend.previous_rate == 0.5 and trend.rate == 0.125


def test_one_decision_waiving_several_requirements_counts_once_and_signals_review():
    logging, network = rule(uuid4()), rule(uuid4())
    decision = uuid4()
    shared = {"decision": decision, "rationale": "Vendor limitation"}
    critical = plan(
        [(logging, {"exception": shared}), (network, {"exception": shared})], criticality="high"
    )
    peer = plan([(logging, {"exception": {"rationale": "vendor  LIMITATION!"}}), (network, {})])
    alternative = plan([(logging, {"option": "alternative"}), (network, {})])
    report = exception_risk(policy(logging, network), "a", [critical, peer, alternative], AS_OF)
    assert report.distinct_exception_decisions == 2
    assert report.solutions_with_exceptions == 2
    kinds = {signal.kind for signal in report.signals}
    assert {"concentration", "recurring_reason", "high_impact_exception", "shared_owner"} <= kinds
    assert report.requirements[0].permitted_alternatives == 1
    assert report.by_criticality == {"high": 1, "unknown": 1}


def test_exception_status_reflects_window_scope_version_and_revocation():
    item = rule(uuid4())
    target = plan([(item, {})]).target
    end = date(2026, 7, 30)
    solution = target.solution_id

    def status(**values) -> str:
        version = values.pop("version", item.version_id)
        return exception_status(waiver(solution, version, **values), target, item.version_id, end)

    assert status() == "active"
    assert status(effective_until=date(2026, 7, 15)) == "expiring"
    assert status(effective_until=date(2026, 3, 31)) == "expired"
    assert status(effective_from=date(2026, 9, 1)) == "future"
    assert status(scope=Scope(environment="test")) == "out_of_scope"
    assert status(version=uuid4()) == "wrong_version"
    assert status(revoked=True) == "revoked"


def test_diff_matches_concepts_and_flags_unresolved_and_publisher_releases():
    kept, changed, removed = rule(uuid4()), rule(uuid4()), rule(uuid4())
    released, added, orphan = rule(uuid4()), rule(uuid4()), rule(uuid4())
    revised = changed.model_copy(
        update={"version_id": uuid4(), "when": WorkloadContext(exposure="private")}
    )
    old = policy(kept, changed, removed, released)
    new = policy(kept, revised, released, added, orphan, version="2")
    references = {
        item.concept_id: reference(item) for item in (kept, revised, removed, released, added)
    }
    changes = {
        item.concept_id: item
        for item in diff_baselines(old, new, references, {released.concept_id: uuid4()})
    }
    assert kept.concept_id not in changes
    assert changes[changed.concept_id].aspects == ("version", "conditions")
    assert changes[removed.concept_id].kind == "removed"
    assert changes[released.concept_id].kind == "publisher_release"
    assert changes[added.concept_id].kind == "added"
    assert changes[orphan.concept_id].kind == "unresolved"


def test_change_impact_classifies_routes_and_is_stable():
    excepted, added, released = rule(uuid4()), rule(uuid4()), rule(uuid4())
    revised = excepted.model_copy(update={"version_id": uuid4()})
    old, new = policy(excepted, released), policy(revised, released, added, version="2")
    references = {item.concept_id: reference(item) for item in (revised, released, added)}
    changes = diff_baselines(old, new, references, {released.concept_id: uuid4()})
    solution = uuid4()
    before = plan([(excepted, {"exception": {}}), (released, {})], solution=solution)
    unrouted = uuid4()
    after = {
        solution: plan([(revised, {}), (released, {}), (added, {})], solution=solution),
        unrouted: plan(
            [(revised, {}), (released, {}), (added, {})],
            solution=unrouted,
            team=None,
            repository=None,
        ),
    }

    def run(stage):
        return change_impact(
            changes,
            {solution: before},
            after,
            stage=stage,
            old=(old, "a"),
            new=(new, "b"),
            as_of=AS_OF,
        )

    report = run("approved_effective")
    found = {(item.solution_id, item.change.concept_id): item for item in report.items}
    carried = found[(solution, excepted.concept_id)]
    assert carried.certainty == "confirmed" and "does not carry" in carried.reason
    assert any(value.startswith("exception:") for value in carried.evidence)
    assert found[(solution, released.concept_id)].certainty == "potential"
    assert found[(unrouted, added.concept_id)].certainty == "confirmed"
    assert (
        "The solution responsible team is not recorded." in found[(unrouted, added.concept_id)].gaps
    )
    assert {draft.route for draft in report.drafts} == {"repo-a", "Owner"}
    assert [item.id for item in run("approved_effective").items] == [
        item.id for item in report.items
    ]
    proposed = run("proposed_adoption")
    assert {item.certainty for item in proposed.items} == {"potential"}
