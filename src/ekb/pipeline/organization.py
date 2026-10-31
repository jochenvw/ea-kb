from __future__ import annotations

import json
from collections.abc import Iterable
from uuid import UUID

from ekb.domain.models import (
    ArchitecturePrincipleSpec,
    Candidate,
    ClassificationProperty,
    ConceptLink,
    Extraction,
    InferenceResult,
    Modality,
    RelationshipKind,
    Scope,
    Segment,
    SemanticType,
    SourceItem,
    SourceRecord,
    SourceRevision,
    content_hash,
    fingerprint,
    stable_id,
)
from ekb.ports import ArtifactStore, UnitOfWorkFactory

CURATION_PARSER = "organization-architecture-v1"


def has_organization_configuration(items: Iterable[SourceItem]) -> bool:
    return any(
        item.metadata.get("enterprise_requirement") or item.metadata.get("architecture_principles")
        for item in items
    )


def apply_source_policy(item: SourceItem, extraction: Extraction) -> Extraction:
    platform_scope = str(item.metadata.get("platform_scope") or "").strip()
    if not platform_scope:
        return extraction
    retained: list[Candidate] = []
    index_map: dict[int, int] = {}
    for index, candidate in enumerate(extraction.candidates):
        if candidate.semantic_type == SemanticType.ARCHITECTURE_DOMAIN:
            continue
        if candidate.semantic_type in {
            SemanticType.CONCEPTUAL_REQUIREMENT,
            SemanticType.TECHNICAL_REQUIREMENT,
            SemanticType.PLATFORM_REQUIREMENT,
        }:
            candidate = candidate.model_copy(
                update={
                    "semantic_type": SemanticType.PLATFORM_REQUIREMENT,
                    "scope": candidate.scope.model_copy(
                        update={"platform": candidate.scope.platform or platform_scope}
                    ),
                }
            )
        index_map[index] = len(retained)
        retained.append(candidate)
    relationships = [
        relationship.model_copy(
            update={
                "from_index": index_map[relationship.from_index],
                "to_index": index_map[relationship.to_index],
                "scope": relationship.scope.model_copy(
                    update={"platform": relationship.scope.platform or platform_scope}
                ),
            }
        )
        for relationship in extraction.relationships
        if relationship.from_index in index_map
        and relationship.to_index in index_map
        and index_map[relationship.from_index] != index_map[relationship.to_index]
    ]
    return Extraction(candidates=retained, relationships=relationships)


def _configuration(
    items: Iterable[SourceItem],
) -> tuple[dict[str, str], list[ArchitecturePrincipleSpec]]:
    requirements: dict[str, str] = {}
    principles: dict[str, ArchitecturePrincipleSpec] = {}
    source_ids = set()
    for item in items:
        source_id = str(item.metadata.get("id") or "")
        if not source_id:
            continue
        source_ids.add(source_id)
        requirement = str(item.metadata.get("enterprise_requirement") or "").strip()
        if requirement:
            requirements[source_id] = requirement
        for raw in item.metadata.get("architecture_principles", []):
            principle = ArchitecturePrincipleSpec.model_validate(raw)
            if principle.id in principles:
                raise ValueError(f"Duplicate architecture principle: {principle.id}")
            principles[principle.id] = principle
    for principle in principles.values():
        if principle.strategic_requirement_source_id not in requirements:
            raise ValueError(
                f"Principle {principle.id} references a source without enterprise_requirement"
            )
        selectors = (principle.agnostic, *principle.controls)
        unknown = sorted({selector.source_id for selector in selectors} - source_ids)
        if unknown:
            raise ValueError(
                f"Principle {principle.id} references unknown source(s): {', '.join(unknown)}"
            )
    return requirements, sorted(principles.values(), key=lambda value: value.id)


def _segment(
    revision_id: UUID,
    ordinal: int,
    text: str,
    heading: str,
    location: dict[str, str],
) -> Segment:
    return Segment(
        id=stable_id(revision_id, CURATION_PARSER, ordinal, text),
        revision_id=revision_id,
        ordinal=ordinal,
        text=text,
        heading=heading,
        location=location,
        quality="curated",
    )


async def organize_knowledge(
    uow: UnitOfWorkFactory,
    artifacts: ArtifactStore,
    items: Iterable[SourceItem],
) -> int:
    requirements, principles = _configuration(items)
    if not requirements and not principles:
        return 0
    configuration = {
        "requirements": requirements,
        "principles": [principle.model_dump(mode="json") for principle in principles],
    }
    content = json.dumps(configuration, sort_keys=True, indent=2).encode()
    digest = content_hash(content)
    source_id = stable_id("organization-architecture-curation")
    revision_id = stable_id(source_id, digest)
    item = SourceItem(
        id=source_id,
        connector="curation:manifest",
        location="ekb://organization/architecture",
        name="Organization architecture curation",
        format="json",
        indicator=digest,
        metadata={"schema": CURATION_PARSER},
    )
    async with uow() as work:
        previous = await work.sources.get(source_id)
    if previous and previous.revision_id == revision_id:
        return 0
    key = await artifacts.put(content)
    revision = SourceRevision(
        id=revision_id,
        source_id=source_id,
        content_hash=digest,
        artifact_key=key,
        predecessor_id=(
            previous.revision_id
            if previous and previous.revision_id and previous.revision_id != revision_id
            else None
        ),
        source_location=item.location,
        metadata_snapshot=configuration,
    )

    segments: list[Segment] = []
    requirement_segments: dict[str, Segment] = {}
    for source_key, statement in sorted(requirements.items()):
        segment = _segment(
            revision_id,
            len(segments),
            statement,
            f"Enterprise requirement: {source_key}",
            {"curation": "enterprise_requirement", "source_id": source_key},
        )
        segments.append(segment)
        requirement_segments[source_key] = segment
    domain_segments: dict[str, Segment] = {}
    for domain in sorted({principle.domain for principle in principles}):
        text = f"Architecture domain: {domain}."
        segment = _segment(
            revision_id,
            len(segments),
            text,
            f"Architecture domain: {domain}",
            {"curation": "architecture_domain", "domain": domain},
        )
        segments.append(segment)
        domain_segments[domain] = segment
    principle_segments: dict[str, Segment] = {}
    for principle in principles:
        segment = _segment(
            revision_id,
            len(segments),
            principle.rationale,
            f"Architecture principle: {principle.id}",
            {"curation": "architecture_principle", "principle_id": principle.id},
        )
        segments.append(segment)
        principle_segments[principle.id] = segment

    async with uow() as work:
        await work.sources.save(
            SourceRecord(
                item=item,
                status="organizing",
                revision_id=previous.revision_id if previous else None,
            )
        )
        await work.sources.save_revision(revision)
        await work.sources.save_segments(revision_id, segments, CURATION_PARSER)
        await work.commit()

    model_id = "organization-curation"
    prompt_version = fingerprint(schema=CURATION_PARSER, content=digest)
    requirement_concepts: dict[str, tuple[UUID, UUID]] = {}
    domain_concepts: dict[str, tuple[UUID, UUID]] = {}
    async with uow() as work:
        for source_key, segment in requirement_segments.items():
            candidate = Candidate(
                statement=requirements[source_key],
                excerpt=requirements[source_key],
                semantic_type=SemanticType.STRATEGIC_REQUIREMENT,
                modality=Modality.UNSPECIFIED,
                confidence=1,
                source_key=source_key,
                uncertainty=(
                    "Organization-curated adoption intent; governance approval remains separate."
                ),
            )
            concept_id = (
                await work.knowledge.persist(
                    segment,
                    InferenceResult(
                        extraction=Extraction(candidates=[candidate]),
                        model_id=model_id,
                        prompt_version=prompt_version,
                    ),
                )
            )[0]
            detail = await work.knowledge.get(concept_id)
            assert detail is not None
            version_id = next(
                occurrence.concept_version_id
                for occurrence in detail.occurrences
                if occurrence.segment_id == segment.id
            )
            requirement_concepts[source_key] = (concept_id, version_id)
        for domain, segment in domain_segments.items():
            candidate = Candidate(
                statement=domain,
                excerpt=domain,
                semantic_type=SemanticType.ARCHITECTURE_DOMAIN,
                confidence=1,
                source_key=domain,
                uncertainty="Organization-curated architecture taxonomy.",
            )
            concept_id = (
                await work.knowledge.persist(
                    segment,
                    InferenceResult(
                        extraction=Extraction(candidates=[candidate]),
                        model_id=model_id,
                        prompt_version=prompt_version,
                    ),
                )
            )[0]
            detail = await work.knowledge.get(concept_id)
            assert detail is not None
            version_id = next(
                occurrence.concept_version_id
                for occurrence in detail.occurrences
                if occurrence.segment_id == segment.id
            )
            domain_concepts[domain] = (concept_id, version_id)
        await work.commit()

    linked: set[tuple[UUID, UUID, str]] = set()
    link_count = 0
    async with uow() as work:
        for principle in principles:
            agnostic_matches = await work.knowledge.match(principle.agnostic)
            if len(agnostic_matches) != 1:
                raise ValueError(
                    f"Principle {principle.id} expected one agnostic match, "
                    f"found {len(agnostic_matches)}"
                )
            agnostic = agnostic_matches[0]
            domain_id, domain_version_id = domain_concepts[principle.domain]
            requirement_id, _ = requirement_concepts[principle.strategic_requirement_source_id]
            links = [
                ConceptLink(
                    from_concept_id=agnostic.concept_id,
                    from_version_id=agnostic.version_id,
                    to_concept_id=domain_id,
                    kind=ClassificationProperty.IN_DOMAIN,
                    excerpt=principle.rationale,
                ),
                ConceptLink(
                    from_concept_id=domain_id,
                    from_version_id=domain_version_id,
                    to_concept_id=requirement_id,
                    kind=RelationshipKind.SUPPORTS,
                    excerpt=principle.rationale,
                ),
            ]
            for selector in principle.controls:
                control_matches = await work.knowledge.match(selector)
                if len(control_matches) != 1:
                    raise ValueError(
                        f"Principle {principle.id} expected one control match for "
                        f"{selector.source_id}/{selector.heading}, "
                        f"found {len(control_matches)}"
                    )
                control = control_matches[0]
                links.append(
                    ConceptLink(
                        from_concept_id=control.concept_id,
                        from_version_id=control.version_id,
                        to_concept_id=agnostic.concept_id,
                        kind=RelationshipKind.REFINES,
                        excerpt=principle.rationale,
                        scope=Scope(platform=control.candidate.scope.platform),
                    )
                )
            for link in links:
                identity = (
                    link.from_concept_id,
                    link.to_concept_id,
                    link.kind.value,
                )
                if identity in linked:
                    continue
                linked.add(identity)
                await work.knowledge.link(
                    principle_segments[principle.id],
                    link,
                    model_id=model_id,
                    prompt_version=prompt_version,
                )
                link_count += 1
        await work.sources.save(
            SourceRecord(item=item, status="available", revision_id=revision_id)
        )
        await work.commit()
    return len(requirement_concepts) + len(domain_concepts) + link_count
