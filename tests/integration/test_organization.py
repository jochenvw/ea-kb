from uuid import uuid4

import pytest

from ekb.adapters.artifacts import FilesystemArtifactStore
from ekb.domain.models import (
    Candidate,
    ConceptSelector,
    Extraction,
    InferenceResult,
    KnowledgeLevel,
    RelationshipKind,
    Scope,
    Segment,
    SemanticType,
    SourceItem,
    SourceRecord,
    SourceRevision,
    content_hash,
    stable_id,
)
from ekb.domain.resolution import Finding
from ekb.pipeline.organization import organize_knowledge

pytestmark = pytest.mark.integration


async def _seed_source(database, source_key, candidates, *, revision_marker=None):
    source_id = stable_id("manifest", source_key)
    revision_id = uuid4()
    item = SourceItem(
        id=source_id,
        connector="test",
        location=f"urn:{source_key}",
        name=source_key,
        format="txt",
        metadata={"id": source_key},
    )
    segments = [
        Segment(
            id=uuid4(),
            revision_id=revision_id,
            ordinal=index,
            text=evidence,
            heading=heading,
        )
        for index, (heading, evidence, _, _) in enumerate(candidates)
    ]
    async with database() as uow:
        previous = await uow.sources.get(source_id)
        await uow.sources.save(
            SourceRecord(
                item=item,
                status="available",
                revision_id=previous.revision_id if previous else revision_id,
            )
        )
        await uow.sources.save_revision(
            SourceRevision(
                id=revision_id,
                source_id=source_id,
                content_hash=content_hash((revision_marker or source_key).encode()),
                artifact_key=source_key,
                predecessor_id=previous.revision_id if previous else None,
            )
        )
        await uow.sources.save_segments(revision_id, segments, "fixture-v1")
        for segment, (_, evidence, semantic_type, scope) in zip(segments, candidates, strict=True):
            await uow.knowledge.persist(
                segment,
                InferenceResult(
                    extraction=Extraction(
                        candidates=[
                            Candidate(
                                statement=evidence,
                                excerpt=evidence,
                                semantic_type=semantic_type,
                                scope=scope,
                            )
                        ]
                    ),
                    model_id="fixture",
                    prompt_version="fixture-v1",
                ),
            )
        await uow.sources.save(SourceRecord(item=item, status="available", revision_id=revision_id))
        await uow.commit()


def _single_principle_items(agnostic_evidence, control_evidence):
    principle = {
        "id": "records",
        "domain": "Records",
        "strategic_requirement_source_id": "standard",
        "agnostic": ConceptSelector(
            source_id="standard",
            heading="Records",
            semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
            excerpt_contains=agnostic_evidence,
        ).model_dump(mode="json"),
        "controls": [
            ConceptSelector(
                source_id="platform",
                heading="Storage",
                semantic_type=SemanticType.PLATFORM_REQUIREMENT,
                excerpt_contains=control_evidence,
            ).model_dump(mode="json")
        ],
        "rationale": "The storage control refines the records requirement.",
    }
    return [
        SourceItem(
            id=stable_id("manifest", "standard"),
            connector="test",
            location="urn:standard",
            name="standard",
            format="txt",
            metadata={
                "id": "standard",
                "enterprise_requirement": "Protect enterprise records.",
                "architecture_principles": [principle],
            },
        ),
        SourceItem(
            id=stable_id("manifest", "platform"),
            connector="test",
            location="urn:platform",
            name="platform",
            format="txt",
            metadata={"id": "platform", "platform_scope": "Azure"},
        ),
    ]


async def test_ten_curated_principles_connect_all_layers_with_provenance(database, tmp_path):
    principles = []
    agnostic_candidates = []
    control_candidates = []
    for index in range(10):
        agnostic_heading = f"agnostic-{index}"
        control_heading = f"control-{index}"
        agnostic_evidence = f"Platform-independent requirement {index}."
        control_evidence = f"Azure control {index}."
        principles.append(
            {
                "id": f"principle-{index}",
                "domain": f"Architecture domain {index}",
                "strategic_requirement_source_id": "standard",
                "agnostic": ConceptSelector(
                    source_id="standard",
                    heading=agnostic_heading,
                    semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
                    excerpt_contains=agnostic_evidence,
                ).model_dump(mode="json"),
                "controls": [
                    ConceptSelector(
                        source_id="platform",
                        heading=control_heading,
                        semantic_type=SemanticType.PLATFORM_REQUIREMENT,
                        excerpt_contains=control_evidence,
                    ).model_dump(mode="json")
                ],
                "rationale": (
                    f"Azure control {index} refines platform-independent requirement {index} "
                    f"within Architecture domain {index} in support of the control baseline."
                ),
            }
        )
        agnostic_candidates.append(
            (
                agnostic_heading,
                agnostic_evidence,
                SemanticType.TECHNICAL_REQUIREMENT,
                Scope(),
            )
        )
        control_candidates.append(
            (
                control_heading,
                control_evidence,
                SemanticType.PLATFORM_REQUIREMENT,
                Scope(platform="Azure"),
            )
        )
    await _seed_source(database, "standard", agnostic_candidates)
    await _seed_source(database, "platform", control_candidates)
    items = [
        SourceItem(
            id=stable_id("manifest", "standard"),
            connector="test",
            location="urn:standard",
            name="standard",
            format="txt",
            metadata={
                "id": "standard",
                "enterprise_requirement": "Adhere to the control baseline.",
                "architecture_principles": principles,
            },
        ),
        SourceItem(
            id=stable_id("manifest", "platform"),
            connector="test",
            location="urn:platform",
            name="platform",
            format="txt",
            metadata={"id": "platform", "platform_scope": "Azure"},
        ),
    ]

    organized = await organize_knowledge(
        database, FilesystemArtifactStore(tmp_path / "artifacts"), items
    )

    assert organized == 41
    assert (
        await organize_knowledge(database, FilesystemArtifactStore(tmp_path / "artifacts"), items)
        == 0
    )
    async with database() as uow:
        company = await uow.knowledge.search(semantic_types=(SemanticType.STRATEGIC_REQUIREMENT,))
        assert [version.candidate.statement for version in company[0].versions] == [
            "Adhere to the control baseline."
        ]
        controls = await uow.knowledge.search(level=KnowledgeLevel.PLATFORM_SPECIFIC, limit=20)
        assert len(controls) == 10
        for control in controls:
            traceability = await uow.inspection.traceability(control.id)
            assert traceability is not None
            assert {node.level for node in traceability.nodes} == {
                KnowledgeLevel.COMPANY,
                KnowledgeLevel.PLATFORM_AGNOSTIC,
                KnowledgeLevel.PLATFORM_SPECIFIC,
            }
            assert {edge.kind for edge in traceability.edges} == {
                "refines",
                "inDomain",
                "supports",
            }
            assert not traceability.truncated
            graph = await uow.inspection.graph(control.id)
            assert graph is not None
            levels = {node.level for node in graph.nodes if node.kind != "source"}
            assert levels == {
                KnowledgeLevel.COMPANY,
                KnowledgeLevel.PLATFORM_AGNOSTIC,
                KnowledgeLevel.PLATFORM_SPECIFIC,
            }
            assert {"refines", "inDomain", "supports"} <= {edge.kind for edge in graph.edges}
            assert all(
                edge.evidence is not None
                for edge in graph.edges
                if edge.kind in {"refines", "inDomain", "supports"}
            )
            assert len({node.id for node in graph.nodes if node.kind == "source"}) == 3

        control = controls[0]
        graph = await uow.inspection.graph(control.id)
        assert graph is not None
        configured = next(
            edge for edge in graph.edges if edge.kind == "refines" and edge.status == "configured"
        )
        assert configured.evidence is not None
        await uow.resolution.save_finding(
            Finding(
                id=uuid4(),
                check_id="manual",
                check_version="1",
                input_revision="test",
                action="relationship",
                concept_ids=(configured.source, configured.target),
                occurrence_ids=(configured.evidence.id,),
                relationship=RelationshipKind.REFINES,
                confidence=1,
                explanation="The configured relationship is already present.",
                deterministic=True,
            )
        )
        await uow.commit()

    async with database() as uow:
        graph = await uow.inspection.graph(control.id)
        assert graph is not None
        refinements = [
            edge
            for edge in graph.edges
            if edge.source == configured.source
            and edge.target == configured.target
            and edge.kind == "refines"
        ]
        assert len(refinements) == 1
        assert refinements[0].status == "configured"
        assert refinements[0].evidence is not None


async def test_concept_selector_filters_before_applying_result_bound(database):
    decoys = [
        (
            f"decoy-{index}",
            f"Unrelated platform-independent requirement {index}.",
            SemanticType.TECHNICAL_REQUIREMENT,
            Scope(),
        )
        for index in range(150)
    ]
    target = (
        "target-control",
        "Networks are segmented into security zones.",
        SemanticType.TECHNICAL_REQUIREMENT,
        Scope(),
    )
    await _seed_source(database, "large-standard", [*decoys, target])

    async with database() as uow:
        matches = await uow.knowledge.match(
            ConceptSelector(
                source_id="large-standard",
                heading=target[0],
                semantic_type=target[2],
                excerpt_contains="segmented into security zones",
            )
        )

    assert len(matches) == 1
    assert matches[0].candidate.statement == target[1]


async def test_curation_revalidates_current_evidence_and_replaces_active_links(database, tmp_path):
    evidence = "Protect stored records."
    agnostic = (
        "Records",
        evidence,
        SemanticType.TECHNICAL_REQUIREMENT,
        Scope(),
    )
    control = (
        "Storage",
        "Stored records are encrypted.",
        SemanticType.PLATFORM_REQUIREMENT,
        Scope(platform="Azure"),
    )
    await _seed_source(database, "standard", [agnostic])
    await _seed_source(database, "platform", [control])
    items = _single_principle_items(evidence, control[1])
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")

    assert await organize_knowledge(database, artifacts, items) == 5
    async with database() as uow:
        selector = ConceptSelector.model_validate(
            items[0].metadata["architecture_principles"][0]["controls"][0]
        )
        control_id = (await uow.knowledge.match(selector))[0].concept_id
        curation_id = stable_id("organization-architecture-curation")
        first_curation = await uow.sources.get(curation_id)
        assert first_curation is not None
    assert await organize_knowledge(database, artifacts, items) == 0

    async with database() as uow:
        standard = await uow.sources.get(stable_id("manifest", "standard"))
        assert standard is not None and standard.revision_id is not None
        parsed_again = Segment(
            id=uuid4(),
            revision_id=standard.revision_id,
            ordinal=0,
            text=evidence,
            heading="Records",
        )
        await uow.sources.save_segments(standard.revision_id, [parsed_again], "fixture-v2")
        await uow.knowledge.persist(
            parsed_again,
            InferenceResult(
                extraction=Extraction(
                    candidates=[
                        Candidate(
                            statement=evidence,
                            excerpt=evidence,
                            semantic_type=SemanticType.TECHNICAL_REQUIREMENT,
                        )
                    ]
                ),
                model_id="fixture",
                prompt_version="fixture-v2",
            ),
        )
        await uow.commit()
    assert await organize_knowledge(database, artifacts, items) == 5
    async with database() as uow:
        parser_curation = await uow.sources.get(curation_id)
        assert parser_curation is not None
        assert parser_curation.revision_id != first_curation.revision_id

    await _seed_source(
        database,
        "standard",
        [agnostic],
        revision_marker="standard-revision-2",
    )
    assert await organize_knowledge(database, artifacts, items) == 5

    async with database() as uow:
        current_curation = await uow.sources.get(curation_id)
        assert current_curation is not None
        assert current_curation.revision_id != parser_curation.revision_id
        revision = await uow.sources.revision(current_curation.revision_id)
        assert revision is not None
        assert revision.predecessor_id == parser_curation.revision_id
        graph = await uow.inspection.graph(control_id)
        assert graph is not None
        assert (
            len(
                [
                    edge
                    for edge in graph.edges
                    if edge.kind == "refines" and edge.status == "configured"
                ]
            )
            == 1
        )
        history = await uow.inspection.source(curation_id)
        assert history is not None
        assert history.revisions == 3


async def test_curation_revalidates_missing_and_ambiguous_evidence(database, tmp_path):
    evidence = "Protect stored records."
    agnostic = (
        "Records",
        evidence,
        SemanticType.TECHNICAL_REQUIREMENT,
        Scope(),
    )
    control = (
        "Storage",
        "Stored records are encrypted.",
        SemanticType.PLATFORM_REQUIREMENT,
        Scope(platform="Azure"),
    )
    await _seed_source(database, "standard", [agnostic])
    await _seed_source(database, "platform", [control])
    items = _single_principle_items(evidence, control[1])
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    assert await organize_knowledge(database, artifacts, items) == 5
    async with database() as uow:
        control_id = (
            await uow.knowledge.match(
                ConceptSelector.model_validate(
                    items[0].metadata["architecture_principles"][0]["controls"][0]
                )
            )
        )[0].concept_id
        curation_id = stable_id("organization-architecture-curation")
        previous_curation = await uow.sources.get(curation_id)
        assert previous_curation is not None

    await _seed_source(
        database,
        "standard",
        [
            (
                "Retention",
                "Delete all expired records.",
                SemanticType.TECHNICAL_REQUIREMENT,
                Scope(),
            )
        ],
        revision_marker="standard-revision-2",
    )
    with pytest.raises(ValueError, match="expected one agnostic match, found 0"):
        await organize_knowledge(database, artifacts, items)
    async with database() as uow:
        current_curation = await uow.sources.get(curation_id)
        assert current_curation is not None
        assert current_curation.revision_id == previous_curation.revision_id
        graph = await uow.inspection.graph(control_id)
        assert graph is not None
        assert not any(edge.status == "configured" for edge in graph.edges)
        trace = await uow.inspection.traceability(control_id)
        assert trace and not any(edge.status == "configured" for edge in trace.edges)

    await _seed_source(
        database,
        "standard",
        [
            agnostic,
            (
                "Records",
                f"{evidence} Keep an audit trail.",
                SemanticType.TECHNICAL_REQUIREMENT,
                Scope(),
            ),
        ],
        revision_marker="standard-revision-3",
    )
    with pytest.raises(ValueError, match="expected one agnostic match, found 2"):
        await organize_knowledge(database, artifacts, items)


async def test_removing_last_curation_retires_configured_links(database, tmp_path):
    evidence = "Protect stored records."
    agnostic = (
        "Records",
        evidence,
        SemanticType.TECHNICAL_REQUIREMENT,
        Scope(),
    )
    control = (
        "Storage",
        "Stored records are encrypted.",
        SemanticType.PLATFORM_REQUIREMENT,
        Scope(platform="Azure"),
    )
    await _seed_source(database, "standard", [agnostic])
    await _seed_source(database, "platform", [control])
    items = _single_principle_items(evidence, control[1])
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    assert await organize_knowledge(database, artifacts, items) == 5
    async with database() as uow:
        control_id = (
            await uow.knowledge.match(
                ConceptSelector.model_validate(
                    items[0].metadata["architecture_principles"][0]["controls"][0]
                )
            )
        )[0].concept_id

    assert await organize_knowledge(database, artifacts, []) == 0
    assert await organize_knowledge(database, artifacts, []) == 0
    async with database() as uow:
        graph = await uow.inspection.graph(control_id)
        assert graph is not None
        assert not any(edge.status == "configured" for edge in graph.edges)
        trace = await uow.inspection.traceability(control_id)
        assert trace and not any(edge.status == "configured" for edge in trace.edges)
        history = await uow.inspection.source(stable_id("organization-architecture-curation"))
        assert history is not None
        assert history.revisions == 2
