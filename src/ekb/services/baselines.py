from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError

from ekb.domain.baselines import BaselinePolicy, BaselineRegistration
from ekb.domain.models import (
    Candidate,
    Extraction,
    InferenceResult,
    PublicationMetadata,
    Segment,
    SemanticType,
    SourceItem,
    SourceRecord,
    SourceRevision,
    content_hash,
    stable_id,
)
from ekb.ports import ArtifactStore, UnitOfWorkFactory
from ekb.services.architectures import REQUIREMENT_TYPES
from ekb.services.assessment_basis import canonical_reference
from ekb.services.knowledge_resolver import KnowledgeResolver
from ekb.services.reconciliation_service import ReconciliationService

CONNECTOR = "baseline"


class BaselineService:
    """Retain an exact baseline policy as evidence and propose, never grant, its adoption."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        artifacts: ArtifactStore,
        resolver: KnowledgeResolver,
        reconciliation: ReconciliationService,
        *,
        max_bytes: int = 1024 * 1024,
    ):
        self.uow = uow
        self.artifacts = artifacts
        self.resolver = resolver
        self.reconciliation = reconciliation
        self.max_bytes = max_bytes

    async def register(self, content: bytes, location: str) -> BaselineRegistration:
        if len(content) > self.max_bytes:
            raise ValueError(f"Baseline exceeds {self.max_bytes} bytes")
        try:
            policy = BaselinePolicy.model_validate_json(content)
        except ValidationError as error:
            raise ValueError(f"Baseline is not a valid policy: {error}") from error
        text = content.decode("utf-8")
        # The approved statement must be exact evidence inside the retained bytes.
        if policy.statement not in text:
            raise ValueError("Baseline statement must appear verbatim (without JSON escapes)")
        async with self.uow() as uow:
            for rule in policy.requirements:
                concept = await uow.knowledge.get(rule.concept_id)
                version = (
                    next((item for item in concept.versions if item.id == rule.version_id), None)
                    if concept
                    else None
                )
                if version is None:
                    raise ValueError(
                        f"Requirement {rule.concept_id} version {rule.version_id} does not exist"
                    )
                if version.candidate.semantic_type not in REQUIREMENT_TYPES:
                    raise ValueError(f"Baseline entry {rule.concept_id} is not a requirement")
        digest = content_hash(content)
        item = SourceItem(
            id=stable_id(CONNECTOR, policy.id),
            connector=CONNECTOR,
            location=location,
            name=f"Baseline {policy.id}",
            format="json",
        )
        revision = SourceRevision(
            id=stable_id(item.id, digest),
            source_id=item.id,
            content_hash=digest,
            artifact_key=digest,
            source_location=location,
            publication=PublicationMetadata(publisher_version=policy.version),
        )
        segment = Segment(
            id=stable_id(revision.id, 0), revision_id=revision.id, ordinal=0, text=text
        )
        if await self.artifacts.put(content) != digest:
            raise ValueError("Artifact store returned a non-content-addressed key")
        async with self.uow() as uow:
            if await uow.sources.revision(revision.id) is None:
                previous = await uow.sources.get(item.id)
                await uow.sources.save(SourceRecord(item=item, revision_id=revision.id))
                await uow.sources.save_revision(
                    revision.model_copy(
                        update={"predecessor_id": previous.revision_id if previous else None}
                    )
                )
                await uow.sources.save_segments(revision.id, [segment], CONNECTOR)
                # The publisher label keeps each policy version a distinct concept version.
                await uow.sources.save_publication(revision.id, revision.publication, CONNECTOR)
            (concept_id,) = await uow.knowledge.persist(
                segment,
                InferenceResult(
                    extraction=Extraction(
                        candidates=[
                            Candidate(
                                statement=policy.statement,
                                excerpt=policy.statement,
                                semantic_type=SemanticType.STRATEGIC_REQUIREMENT,
                                scope=policy.scope,
                                source_key=f"{policy.id}@{policy.version}",
                                confidence=1.0,
                            )
                        ]
                    ),
                    model_id=CONNECTOR,
                    prompt_version="baseline-v1",
                ),
            )
            await uow.commit()
            concept = await uow.knowledge.get(concept_id)
        assert concept is not None
        version_id = next(
            item.id
            for item in concept.versions
            if item.publication.publisher_version == policy.version
        )
        proposals: list[UUID] = []
        pending = [(concept_id, version_id, policy.scope)] + [
            (rule.concept_id, rule.version_id, rule.scope) for rule in policy.requirements
        ]
        for pending_id, pending_version, scope in pending:
            resolution = await self.resolver.resolve_concept(pending_id, scope)
            if canonical_reference(resolution, pending_version) is None:
                finding = await self.reconciliation.propose_canonical(
                    pending_id, pending_version, scope, actor="baseline-registration"
                )
                proposals.append(finding.id)
        return BaselineRegistration(
            concept_id=concept_id,
            version_id=version_id,
            policy_id=policy.id,
            policy_version=policy.version,
            policy_sha256=digest,
            requirements=len(policy.requirements),
            pending_approvals=tuple(proposals),
        )
