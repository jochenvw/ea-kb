from datetime import date
from uuid import UUID

from ekb.domain.assessment import AssessmentReference
from ekb.domain.baselines import BaselineApproval
from ekb.domain.portfolio import (
    ChangeImpactInput,
    ChangeImpactReport,
    ChangeStage,
    ExceptionRiskInput,
    ExceptionRiskReport,
    change_impact,
    compare,
    diff_baselines,
    exception_risk,
)
from ekb.services.assessment_basis import canonical_reference
from ekb.services.solution_assessment import SolutionAssessmentService


class PortfolioService:
    """Read-only portfolio projections; callers publish results, nothing is persisted."""

    def __init__(self, assessments: SolutionAssessmentService, *, max_solutions: int = 500):
        self.assessments = assessments
        self.max_solutions = max_solutions

    def _truncation(self, truncated: bool) -> list[str]:
        if not truncated:
            return []
        return [f"The portfolio was truncated at {self.max_solutions} solutions."]

    async def _risk(self, approval: BaselineApproval, request: ExceptionRiskInput, as_of: date):
        policy, digest, root_bound, plans, truncated = await self.assessments.plans(
            approval, as_of, max_solutions=self.max_solutions
        )
        gaps = self._truncation(truncated)
        if not root_bound and plans:
            gaps.append("The baseline is not an approved, effective enterprise requirement.")
        return exception_risk(
            policy,
            digest,
            plans,
            as_of,
            window_days=request.window_days,
            review_rate=request.review_rate,
            gaps=gaps,
        )

    async def exception_risk(self, request: ExceptionRiskInput) -> ExceptionRiskReport:
        approval = BaselineApproval(
            concept_id=request.baseline_concept_id, version_id=request.baseline_version_id
        )
        as_of = request.as_of or date.today()
        report = await self._risk(approval, request, as_of)
        if request.previous_as_of is None:
            return report
        if request.previous_as_of >= as_of:
            raise ValueError("previous_as_of must precede as_of")
        try:
            previous = await self._risk(approval, request, request.previous_as_of)
        except ValueError:
            gaps = (*report.gaps, "The baseline had no evidence at previous_as_of.")
            return report.model_copy(update={"gaps": gaps})
        return compare(report, previous)

    async def _releases(self, references: dict[UUID, AssessmentReference | None], as_of: date):
        """Newer concept versions the baseline has not pinned: potential impact only."""
        releases: dict[UUID, UUID] = {}
        for concept_id, reference in references.items():
            if reference is None:
                continue
            resolution = await self.assessments.resolver.resolve_concept(concept_id, as_of=as_of)
            pinned = next(
                (item for item in resolution.versions if item.id == reference.version_id), None
            )
            newer = [
                item
                for item in resolution.versions
                if pinned and item.id != pinned.id and item.recorded_at > pinned.recorded_at
            ]
            if newer:
                releases[concept_id] = max(newer, key=lambda item: item.recorded_at).id
        return releases

    async def change_impact(self, request: ChangeImpactInput) -> ChangeImpactReport:
        as_of = request.as_of or date.today()
        effective = request.effective_from or as_of
        old = BaselineApproval(concept_id=request.old_concept_id, version_id=request.old_version_id)
        new = BaselineApproval(concept_id=request.new_concept_id, version_id=request.new_version_id)
        old_policy, old_digest, _, old_plans, old_cut = await self.assessments.plans(
            old, as_of, max_solutions=self.max_solutions
        )
        # A future effective date evaluates authority then; current solutions remain in scope.
        evaluated = max(effective, as_of) if request.stage == "approved" else as_of
        new_policy, new_digest, root_bound, new_plans, new_cut = await self.assessments.plans(
            new, evaluated, max_solutions=self.max_solutions
        )
        gaps = self._truncation(old_cut or new_cut)
        stage: ChangeStage = "proposed_adoption"
        if request.stage == "approved":
            if root_bound:
                stage = "approved_effective" if effective <= as_of else "approved_not_yet_effective"
            else:
                gaps.append("The new baseline is not approved and effective; treated as proposed.")
        references: dict[UUID, AssessmentReference | None] = {}
        for rule in (*new_policy.requirements, *old_policy.requirements):
            if rule.concept_id in references:
                continue
            resolution = await self.assessments.resolver.resolve_concept(
                rule.concept_id, as_of=evaluated
            )
            references[rule.concept_id] = canonical_reference(resolution, rule.version_id)
        if not new_plans:
            gaps.append("No registered solutions were recorded by the evaluation date.")
        changes = diff_baselines(
            old_policy, new_policy, references, await self._releases(references, evaluated)
        )
        return change_impact(
            changes,
            {plan.solution_id: plan for plan in old_plans},
            {plan.solution_id: plan for plan in new_plans},
            stage=stage,
            old=(old_policy, old_digest),
            new=(new_policy, new_digest),
            as_of=as_of,
            effective_from=request.effective_from,
            gaps=gaps,
        )
