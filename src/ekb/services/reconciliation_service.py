from uuid import UUID

from ekb.domain.models import Scope, fingerprint, now, stable_id
from ekb.domain.resolution import Decision, Finding, version_evidence
from ekb.observability import tracer
from ekb.ports import UnitOfWorkFactory


class ReconciliationService:
    def __init__(self, database: UnitOfWorkFactory):
        self.database = database

    async def propose(self, finding: Finding) -> Finding:
        with tracer.start_as_current_span("reconciliation.propose"):
            async with self.database() as uow:
                await uow.resolution.save_finding(finding)
                await uow.commit()
        return finding

    async def findings(
        self,
        limit: int = 100,
        *,
        check_id: str | None = None,
        after: UUID | None = None,
    ) -> list[Finding]:
        async with self.database() as uow:
            return await uow.resolution.findings(limit, check_id=check_id, after=after)

    async def get(self, finding_id: UUID) -> Finding | None:
        async with self.database() as uow:
            return await uow.resolution.finding(finding_id)

    async def history(self, finding_id: UUID | None = None) -> list[Decision]:
        async with self.database() as uow:
            return await uow.resolution.decisions(finding_id)

    async def decide(self, decision: Decision) -> Decision:
        with tracer.start_as_current_span(
            "reconciliation.apply_decision",
            attributes={
                "finding.id": str(decision.finding_id),
                "decision.outcome": decision.outcome,
            },
        ):
            async with self.database() as uow:
                result = await uow.resolution.apply_decision(
                    decision.model_copy(update={"recorded_at": now()})
                )
                await uow.commit()
        return result

    async def propose_canonical(
        self,
        concept_id: UUID,
        version_id: UUID,
        scope: Scope,
        allow_prerelease: bool = False,
        actor: str = "local-operator",
    ) -> Finding:
        async with self.database() as uow:
            concept = await uow.knowledge.get(concept_id)
        if concept is None or version_id not in {v.id for v in concept.versions}:
            raise ValueError("Select an existing concept version")
        refs = version_evidence(concept, version_id)
        proposal = Finding(
            id=stable_id(
                "canonical-review",
                concept_id,
                version_id,
                fingerprint(scope=scope.model_dump(), allow_prerelease=allow_prerelease),
            ),
            check_id="manual",
            check_version="1",
            input_revision=fingerprint(version=version_id),
            action="canonical_review",
            concept_ids=(concept_id,),
            version_ids=(version_id,),
            occurrence_ids=refs,
            scope=scope,
            confidence=1.0,
            explanation="Explicit scoped canonical selection; requires a recorded human decision.",
            allow_prerelease=allow_prerelease,
            proposed_by=actor,
        )
        return await self.propose(proposal)
