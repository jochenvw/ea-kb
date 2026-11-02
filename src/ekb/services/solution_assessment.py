import asyncio
import json
from datetime import date

from pydantic import ValidationError

from ekb.domain.architectures import SolutionArchitectureRevision
from ekb.domain.assessment import (
    AssessmentTarget,
    SolutionAssessment,
    SolutionAssessmentInput,
    assess_solution,
)
from ekb.domain.baselines import (
    BaselineApproval,
    BaselinePolicy,
    DesignDocument,
    SolutionIdentity,
    WorkloadContext,
    with_observations,
)
from ekb.domain.models import content_hash
from ekb.domain.portfolio import SolutionPlan
from ekb.ports import ArtifactStore, ImplementationAnalyzer
from ekb.services.architectures import ArchitectureService
from ekb.services.assessment_basis import AssessmentBasis, AssessmentBasisService
from ekb.services.knowledge_resolver import KnowledgeResolver


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class SolutionAssessmentService:
    """One read-only assessment path shared by MCP, HTTP, CLI and continuous integration."""

    def __init__(
        self,
        architectures: ArchitectureService,
        resolver: KnowledgeResolver,
        artifacts: ArtifactStore,
        analyzer: ImplementationAnalyzer,
        *,
        max_input_bytes: int = 5 * 1024 * 1024,
    ):
        self.architectures = architectures
        self.resolver = resolver
        self.artifacts = artifacts
        self.analyzer = analyzer
        self.max_input_bytes = max_input_bytes

    async def policy(self, approval: BaselineApproval, as_of: date) -> tuple[BaselinePolicy, str]:
        """Load the exact approved baseline bytes, not a similarly named document."""
        resolution = await self.resolver.resolve_concept(approval.concept_id, as_of=as_of)
        revision = next(
            (
                item.revision
                for item in resolution.provenance
                if item.occurrence.concept_version_id == approval.version_id
                and item.occurrence.extraction_metadata.get("purpose") != "relationship"
            ),
            None,
        )
        if revision is None:
            raise ValueError("The approved baseline version has no source evidence")
        content = await self.artifacts.get(revision.artifact_key)
        if content_hash(content) != revision.content_hash:
            raise ValueError("The approved baseline artefact does not match its recorded hash")
        try:
            return BaselinePolicy.model_validate_json(content), revision.content_hash
        except ValidationError as error:
            raise ValueError("The approved baseline is not a machine-readable policy") from error

    async def plan(
        self,
        revision: SolutionArchitectureRevision,
        raw: bytes,
        approval: BaselineApproval,
        policy: BaselinePolicy,
        policy_sha256: str,
        as_of: date,
        *,
        artifact_sha256: str | None = None,
        parameters_sha256: str | None = None,
        commit: str | None = None,
    ) -> tuple[SolutionPlan, AssessmentBasis]:
        """Plan every baseline requirement for one exact architecture revision."""
        try:
            design = DesignDocument.model_validate_json(raw)
        except ValidationError:
            design = None
        identity = design.solution if design else SolutionIdentity()
        target = AssessmentTarget(
            solution_id=revision.architecture_id,
            architecture_revision_id=revision.id,
            # Without implementation input, the design artefact is the assessed basis.
            artifact_sha256=artifact_sha256 or revision.content_hash,
            parameters_sha256=parameters_sha256,
            commit=commit,
            scope=revision.scope,
            as_of=as_of,
            owner=identity.owner or revision.owner,
            team=identity.team,
            repository=identity.repository,
        )
        basis = await AssessmentBasisService(self.resolver).prepare(
            policy, policy_sha256, approval, revision, design, target
        )
        plan = SolutionPlan(
            solution_id=revision.architecture_id,
            revision_id=revision.id,
            version=revision.version,
            name=identity.name or revision.name,
            design_available=design is not None,
            context=design.context if design else WorkloadContext(),
            target=target,
            requirements=basis.requirements,
            gaps=basis.gaps,
        )
        return plan, basis

    async def plans(
        self, approval: BaselineApproval, as_of: date, *, max_solutions: int = 500
    ) -> tuple[BaselinePolicy, str, bool, tuple[SolutionPlan, ...], bool]:
        """Plan the latest revision recorded by `as_of` for every registered solution.

        Returns policy, policy hash, whether the baseline root is bound, the plans and whether
        the portfolio was truncated at `max_solutions`.
        """
        policy, policy_sha256 = await self.policy(approval, as_of)
        plans: list[SolutionPlan] = []
        root_bound = False
        after = None
        truncated = False
        while True:
            page = await self.architectures.list(100, after=after)
            for architecture in page:
                revisions = [
                    item for item in architecture.revisions if item.recorded_at.date() <= as_of
                ]
                if not revisions:
                    continue
                if len(plans) >= max_solutions:
                    truncated = True
                    break
                revision, raw = await self.architectures.artifact(architecture.id, revisions[-1].id)
                plan, basis = await self.plan(revision, raw, approval, policy, policy_sha256, as_of)
                root_bound = root_bound or basis.root_bound
                plans.append(plan)
            if truncated or len(page) < 100:
                break
            after = page[-1].id
        return policy, policy_sha256, root_bound, tuple(plans), truncated

    async def assess(self, request: SolutionAssessmentInput) -> SolutionAssessment:
        template = canonical_json(request.template)
        parameters = canonical_json(request.parameters)
        if len(template) + len(parameters) > self.max_input_bytes:
            raise ValueError(f"Implementation inputs exceed {self.max_input_bytes} bytes")
        architecture = await self.architectures.get(request.architecture_id)
        if architecture is None:
            raise ValueError("Solution architecture not found")
        revisions = [
            item
            for item in architecture.revisions
            if request.version is None or item.version == request.version
        ]
        if not revisions:
            raise ValueError("Solution architecture version not found")
        revision, raw = await self.architectures.artifact(architecture.id, revisions[-1].id)
        as_of = request.as_of or date.today()
        approval = BaselineApproval(
            concept_id=request.baseline_concept_id, version_id=request.baseline_version_id
        )
        policy, policy_sha256 = await self.policy(approval, as_of)
        plan, basis = await self.plan(
            revision,
            raw,
            approval,
            policy,
            policy_sha256,
            as_of,
            artifact_sha256=content_hash(template),
            parameters_sha256=content_hash(parameters),
            commit=request.commit,
        )
        target = plan.target
        checks = [item.check for item in basis.requirements if item.check]
        # Template evaluation is synchronous CPU work; keep it off the event loop.
        observations = await asyncio.to_thread(
            self.analyzer.observe,
            request.template,
            request.parameters,
            checks,
            artifact_sha256=target.artifact_sha256,
            basis=target.basis,
        )
        result = assess_solution(
            target,
            with_observations(basis.requirements, observations),
            baseline_revision=policy_sha256,
            baseline_complete=basis.complete,
            baseline_completeness=basis.completeness,
            basis_fingerprint=basis.fingerprint,
            gaps=basis.gaps,
        )
        findings = []
        for finding in result.findings:
            chain = (
                await self.architectures.chain(
                    finding.requirement.concept_id, finding.requirement.version_id
                )
                if finding.authoritative
                else ()
            )
            findings.append(finding.model_copy(update={"chain": chain}))
        return result.model_copy(update={"findings": tuple(findings)})
