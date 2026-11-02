from __future__ import annotations

from datetime import date
from uuid import UUID

from ekb.domain.architectures import (
    ArchitectureAssessment,
    ArchitectureControl,
    ArchitectureEvidence,
    ArchitectureManifest,
    ArchitectureRequirementDetail,
    AssessmentOutcome,
    ControlAssessment,
    SolutionArchitecture,
    SolutionArchitectureRevision,
    TraceabilityStep,
    coverage_outcome,
)
from ekb.domain.models import (
    ClassificationProperty,
    KnowledgeLevel,
    RelationshipKind,
    SemanticType,
    content_hash,
    fingerprint,
    knowledge_level,
    now,
    stable_id,
)
from ekb.ports import ArtifactStore, UnitOfWork, UnitOfWorkFactory

REQUIREMENT_TYPES = {
    SemanticType.CONCEPTUAL_REQUIREMENT,
    SemanticType.TECHNICAL_REQUIREMENT,
    SemanticType.PLATFORM_REQUIREMENT,
}
LINEAGE_KINDS = {
    RelationshipKind.REFINES.value,
    RelationshipKind.SUPPORTS.value,
    ClassificationProperty.IN_DOMAIN.value,
}


class ArchitectureService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        artifacts: ArtifactStore | None = None,
        *,
        max_artifact_bytes: int = 100 * 1024 * 1024,
    ):
        self.uow = uow
        self.artifacts = artifacts
        self.max_artifact_bytes = max_artifact_bytes

    async def register(
        self,
        manifest: ArchitectureManifest,
        artifact_name: str,
        media_type: str,
        content: bytes,
    ) -> SolutionArchitecture:
        if self.artifacts is None:
            raise ValueError("Architecture artifact writes are not configured")
        if not content:
            raise ValueError("Architecture artifact is empty")
        if len(content) > self.max_artifact_bytes:
            raise ValueError(f"Architecture artifact exceeds {self.max_artifact_bytes} bytes")
        digest = content_hash(content)
        architecture_id = manifest.architecture_id or stable_id(
            "solution-architecture", manifest.canonical_source
        )
        async with self.uow() as uow:
            controls = []
            for control in manifest.controls:
                concept = await uow.knowledge.get(control.concept_id)
                if concept is None:
                    raise ValueError(f"Architecture control {control.concept_id} does not exist")
                version = (
                    concept.versions[-1]
                    if control.concept_version_id is None
                    else next(
                        (
                            item
                            for item in concept.versions
                            if item.id == control.concept_version_id
                        ),
                        None,
                    )
                )
                if version is None:
                    raise ValueError(
                        f"Architecture control version {control.concept_version_id} "
                        f"does not belong to concept {control.concept_id}"
                    )
                semantic_type = version.candidate.semantic_type
                if semantic_type not in REQUIREMENT_TYPES:
                    raise ValueError("Architecture controls must reference requirement concepts")
                controls.append(control.model_copy(update={"concept_version_id": version.id}))
            pinned_manifest = manifest.model_copy(update={"controls": tuple(controls)})
            revision_id = stable_id(
                architecture_id,
                manifest.version,
                digest,
                artifact_name,
                media_type,
                fingerprint(manifest=pinned_manifest.model_dump(mode="json")),
            )
            revision = SolutionArchitectureRevision(
                id=revision_id,
                architecture_id=architecture_id,
                name=manifest.name,
                description=manifest.description,
                owner=manifest.owner,
                canonical_source=manifest.canonical_source,
                version=manifest.version,
                scope=manifest.scope,
                artifact_key=digest,
                artifact_name=artifact_name,
                media_type=media_type,
                content_hash=digest,
                controls=tuple(controls),
                recorded_at=now(),
            )
            existing = await uow.architectures.get(architecture_id)
            if existing is not None:
                matching = next(
                    (item for item in existing.revisions if item.version == manifest.version),
                    None,
                )
                if matching is not None:
                    if matching.id != revision.id:
                        raise ValueError(
                            "Architecture version already exists with different content or controls"
                        )
                    return existing
        artifact_key = await self.artifacts.put(content)
        if artifact_key != digest:
            raise ValueError("Architecture artifact store returned a non-content-addressed key")
        async with self.uow() as uow:
            architecture = await uow.architectures.save(revision)
            await uow.commit()
        return architecture

    async def list(
        self, limit: int = 50, *, after: UUID | None = None
    ) -> list[SolutionArchitecture]:
        async with self.uow() as uow:
            return await uow.architectures.list(limit, after=after)

    async def get(self, architecture_id: UUID) -> SolutionArchitecture | None:
        async with self.uow() as uow:
            return await uow.architectures.get(architecture_id)

    async def requirements(
        self, architecture_id: UUID, *, version: str | None = None
    ) -> tuple[ArchitectureRequirementDetail, ...]:
        async with self.uow() as uow:
            architecture = await uow.architectures.get(architecture_id)
            if architecture is None:
                raise ValueError("Solution architecture not found")
            revisions = [
                item
                for item in architecture.revisions
                if version is None or item.version == version
            ]
            if not revisions:
                raise ValueError("Solution architecture version not found")
            details = []
            for control in revisions[-1].controls:
                concept = await uow.knowledge.get(control.concept_id)
                if concept is None:
                    raise ValueError(f"Architecture control {control.concept_id} no longer exists")
                if not any(item.id == control.concept_version_id for item in concept.versions):
                    raise ValueError(
                        "Architecture control version "
                        f"{control.concept_version_id} no longer exists"
                    )
                details.append(
                    ArchitectureRequirementDetail(
                        control=control,
                        concept=concept,
                    )
                )
        return tuple(details)

    async def artifact(
        self, architecture_id: UUID, revision_id: UUID
    ) -> tuple[SolutionArchitectureRevision, bytes]:
        if self.artifacts is None:
            raise ValueError("Architecture artifact reads are not configured")
        async with self.uow() as uow:
            revision = await uow.architectures.revision(revision_id)
        if revision is None or revision.architecture_id != architecture_id:
            raise ValueError("Solution architecture revision not found")
        return revision, await self.artifacts.get(revision.artifact_key)

    async def assess(
        self,
        architecture_id: UUID,
        evidence: tuple[ArchitectureEvidence, ...],
        *,
        version: str | None = None,
        as_of: date | None = None,
    ) -> ArchitectureAssessment:
        if len({item.concept_id for item in evidence}) != len(evidence):
            raise ValueError("Assessment evidence cannot repeat a control")
        effective = as_of or date.today()
        async with self.uow() as uow:
            architecture = await uow.architectures.get(architecture_id)
            if architecture is None:
                raise ValueError("Solution architecture not found")
            revisions = [
                item
                for item in architecture.revisions
                if version is None or item.version == version
            ]
            if not revisions:
                raise ValueError("Solution architecture version not found")
            revision = revisions[-1]
            evidence_by_control = {item.concept_id: item for item in evidence}
            registered = {control.concept_id for control in revision.controls}
            assessments = []
            for control in revision.controls:
                declared = evidence_by_control.get(control.concept_id)
                outcome = coverage_outcome(control, declared, effective)
                chain = await self._chain(uow, control)
                assessments.append(
                    ControlAssessment(
                        concept_id=control.concept_id,
                        outcome=outcome,
                        evidence=declared,
                        exception=control if outcome is AssessmentOutcome.EXCEPTED else None,
                        chain=chain,
                        explanation=self._explanation(outcome, declared, control, chain),
                    )
                )
        return ArchitectureAssessment(
            architecture_id=architecture_id,
            revision_id=revision.id,
            version=revision.version,
            as_of=effective,
            conforms=all(item.outcome is not AssessmentOutcome.MISSING for item in assessments),
            controls=tuple(assessments),
            unregistered_evidence=tuple(
                item for item in evidence if item.concept_id not in registered
            ),
        )

    async def _chain(
        self, uow: UnitOfWork, control: ArchitectureControl
    ) -> tuple[TraceabilityStep, ...]:
        concept = await uow.knowledge.get(control.concept_id)
        pinned = (
            next(
                (item for item in concept.versions if item.id == control.concept_version_id),
                None,
            )
            if concept
            else None
        )
        if pinned is None:
            raise ValueError("Architecture control version no longer exists")
        graph = await uow.inspection.graph(control.concept_id)
        if graph is None:
            return ()
        edges = [
            edge
            for edge in graph.edges
            if edge.kind in LINEAGE_KINDS and edge.status not in {"proposal", "proposed"}
        ]
        bound_edges = []
        for edge in edges:
            if edge.source == control.concept_id:
                if edge.evidence and edge.evidence.concept_version_id != pinned.id:
                    continue
                if not edge.evidence and edge.href.startswith("/review?finding_id="):
                    proposal = await uow.resolution.finding(UUID(edge.href.split("=", 1)[1]))
                    if proposal is None or pinned.id not in proposal.version_ids:
                        continue
            bound_edges.append(edge)
        distance = {graph.root: 0}
        queue = [graph.root]
        while queue:
            child = queue.pop(0)
            for edge in bound_edges:
                if edge.source != child or edge.target in distance:
                    continue
                distance[edge.target] = distance[child] + 1
                queue.append(edge.target)
        nodes = {
            node.id: node for node in graph.nodes if node.kind != "source" and node.id in distance
        }
        ordered = sorted(
            nodes.values(),
            key=lambda node: (
                0
                if node.level == KnowledgeLevel.COMPANY
                else 1
                if node.level == KnowledgeLevel.PLATFORM_AGNOSTIC
                else 2,
                -(distance.get(node.id) or 0),
                node.label.casefold(),
            ),
        )
        return tuple(
            TraceabilityStep(
                concept_id=node.id,
                level=knowledge_level(pinned.candidate)
                if node.id == control.concept_id
                else KnowledgeLevel(node.level or KnowledgeLevel.UNCLASSIFIED),
                statement=pinned.candidate.statement
                if node.id == control.concept_id
                else node.label,
            )
            for node in ordered
        )

    @staticmethod
    def _explanation(
        outcome: AssessmentOutcome,
        evidence: ArchitectureEvidence | None,
        control: ArchitectureControl,
        chain: tuple[TraceabilityStep, ...],
    ) -> str:
        path = " > ".join(" ".join(step.statement.split())[:120] for step in chain)
        if outcome is AssessmentOutcome.CONFORMS:
            basis = evidence.summary if evidence else "Declared evidence supplied"
            return f"OK: {basis}. Chain: {path}"
        if outcome is AssessmentOutcome.EXCEPTED:
            expiry = f" through {control.expires_on.isoformat()}" if control.expires_on else ""
            return (
                f"OK by exception approved by {control.approved_by}{expiry}: "
                f"{control.rationale}. Chain: {path}"
            )
        return f"Missing: no declared evidence or active exception. Chain: {path}"
