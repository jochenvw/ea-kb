from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from typing import Literal
from uuid import UUID

from pydantic import Field

from ekb.domain.models import (
    ClassificationProperty,
    ConceptDetail,
    ConceptRelationshipKind,
    ConceptVersion,
    KnowledgeLevel,
    Model,
    RelationshipKind,
    SemanticType,
    Usage,
    fingerprint,
    knowledge_level,
    stable_id,
)
from ekb.domain.reconciliation import (
    CachedTraceability,
    SemanticAssertion,
    SemanticCacheEntry,
    Verification,
)
from ekb.domain.resolution import Finding, decision_heads, version_evidence
from ekb.domain.traceability import TraceabilityAssertion, TraceabilityVerification
from ekb.ports import (
    EmbeddingPort,
    SemanticVerifier,
    TraceabilityMapper,
    UnitOfWorkFactory,
)
from ekb.ports.workflow import CheckContext, CheckResult
from ekb.services.retrieval import cosine
from ekb.stages.enrichment import Check, current, finding
from ekb.stages.semantic import _embeddings, _verification

_AGNOSTIC_REQUIREMENTS = frozenset(
    {SemanticType.CONCEPTUAL_REQUIREMENT, SemanticType.TECHNICAL_REQUIREMENT}
)
_COMPANY_TYPES = frozenset({SemanticType.STRATEGIC_GOAL, SemanticType.STRATEGIC_REQUIREMENT})
_TRACE_RELATIONSHIPS = frozenset(
    {
        RelationshipKind.REFINES,
        RelationshipKind.SUPPORTS,
        ClassificationProperty.IN_DOMAIN,
    }
)


@dataclass(frozen=True)
class TraceEdge:
    source: UUID
    target: UUID
    kind: ConceptRelationshipKind


class ControlMappingOptions(Model):
    confidence_threshold: float = Field(default=0.72, ge=0, le=1)
    max_parents: int = Field(default=3, ge=1, le=3)


def _entries(
    context: CheckContext,
) -> dict[UUID, tuple[ConceptDetail, ConceptVersion]]:
    return {concept.id: (concept, version) for concept, version in current(context)}


def assertion(
    concept: ConceptDetail,
    version: ConceptVersion,
) -> TraceabilityAssertion:
    occurrence_ids = version_evidence(concept, version.id)
    occurrences = {occurrence.id: occurrence for occurrence in concept.occurrences}
    occurrence = occurrences[occurrence_ids[0]]
    semantic_type = version.candidate.semantic_type
    if semantic_type is None:
        raise ValueError("Traceability assertions require an ontology classification")
    return TraceabilityAssertion(
        concept_id=concept.id,
        version_id=version.id,
        occurrence_id=occurrence.id,
        statement=version.candidate.statement,
        excerpt=occurrence.excerpt,
        semantic_type=semantic_type,
        scope=version.candidate.scope,
    )


def active_findings(context: CheckContext) -> tuple[Finding, ...]:
    heads = decision_heads(context.snapshot.decisions)
    by_id = {
        finding.id: finding
        for finding in context.snapshot.findings
        if finding.id in context.snapshot.active_finding_ids
    }
    by_id.update({finding.id: finding for finding in context.prior_findings})
    return tuple(
        finding
        for finding in by_id.values()
        if finding.id not in heads or heads[finding.id].outcome != "rejected"
    )


def relationship_edges(
    context: CheckContext,
    *,
    exclude_checks: frozenset[str] = frozenset(),
) -> set[TraceEdge]:
    edges = {
        TraceEdge(
            relationship.from_concept_id,
            relationship.to_concept_id,
            relationship.kind,
        )
        for concept in context.snapshot.concepts
        for relationship in concept.relationships
        if relationship.kind in _TRACE_RELATIONSHIPS
    }
    for proposal in active_findings(context):
        if proposal.check_id in exclude_checks:
            continue
        relationship = proposal.relationship
        if (
            proposal.action == "relationship"
            and relationship in _TRACE_RELATIONSHIPS
            and relationship is not None
            and len(proposal.concept_ids) >= 2
        ):
            edges.add(TraceEdge(proposal.concept_ids[0], proposal.concept_ids[1], relationship))
    return edges


def reachable(
    source: UUID,
    targets: set[UUID],
    edges: set[TraceEdge],
    *,
    max_depth: int = 4,
) -> set[UUID]:
    adjacency: dict[UUID, set[UUID]] = defaultdict(set)
    for edge in edges:
        adjacency[edge.source].add(edge.target)
    found: set[UUID] = set()
    frontier = {source}
    visited = {source}
    for _ in range(max_depth):
        next_frontier = {target for node in frontier for target in adjacency.get(node, set())}
        found.update(next_frontier & targets)
        frontier = next_frontier - visited
        if not frontier:
            break
        visited.update(frontier)
    return found


async def company_alignment(
    context: CheckContext,
    source_ids_by_concept: dict[UUID, set[UUID]] | None = None,
) -> CheckResult:
    entries = _entries(context)
    company = {
        concept_id
        for concept_id, (_, version) in entries.items()
        if version.candidate.semantic_type in _COMPANY_TYPES
    }
    agnostic = {
        concept_id
        for concept_id, (_, version) in entries.items()
        if knowledge_level(version.candidate) is KnowledgeLevel.PLATFORM_AGNOSTIC
    }
    source_anchors: dict[UUID, list[UUID]] = defaultdict(list)
    for concept_id in company:
        _, version = entries[concept_id]
        if (
            version.candidate.semantic_type is SemanticType.STRATEGIC_REQUIREMENT
            and version.candidate.source_key
        ):
            source_anchors[stable_id("manifest", version.candidate.source_key)].append(concept_id)

    edges = relationship_edges(context, exclude_checks=frozenset({"company_alignment"}))
    results: list[Finding] = []
    for concept_id in sorted(agnostic, key=str):
        if reachable(concept_id, company, edges):
            continue
        concept, version = entries[concept_id]
        parents = {
            parent
            for source_id in (
                source_ids_by_concept.get(concept_id, set())
                if source_ids_by_concept is not None
                else {source.id for source in concept.sources}
            )
            for parent in source_anchors.get(source_id, ())
        }
        relation: ConceptRelationshipKind = (
            RelationshipKind.SUPPORTS
            if version.candidate.semantic_type is SemanticType.ARCHITECTURE_DOMAIN
            else RelationshipKind.REFINES
        )
        child_assertion = assertion(concept, version)
        for parent_id in sorted(parents, key=str):
            parent_concept, parent_version = entries[parent_id]
            parent_assertion = assertion(parent_concept, parent_version)
            result = finding(
                context,
                "company_alignment",
                context.check_version,
                action="relationship",
                concept_ids=(concept_id, parent_id),
                version_ids=(version.id, parent_version.id),
                occurrence_ids=(
                    child_assertion.occurrence_id,
                    parent_assertion.occurrence_id,
                ),
                relationship=relation,
                scope=version.candidate.scope,
                confidence=1.0,
                deterministic=True,
                explanation=(
                    "This platform-independent concept comes from a source explicitly adopted by "
                    "the organization-level strategic requirement."
                ),
                uncertainty=(
                    "Enterprise adoption is configured; governance approval remains separate."
                ),
                proposed_by="rule:source-adoption",
            )
            results.append(result)
            edges.add(TraceEdge(concept_id, parent_id, relation))

    parented = {concept_id for concept_id in agnostic if reachable(concept_id, company, edges)}
    company_with_children = {
        parent_id for concept_id in agnostic for parent_id in reachable(concept_id, company, edges)
    }
    return CheckResult(
        findings=tuple(results),
        candidates=len(agnostic),
        statistics={
            "company_total": len(company),
            "company_with_children": len(company_with_children),
            "company_without_children": len(company - company_with_children),
            "agnostic_total": len(agnostic),
            "agnostic_parented": len(parented),
            "agnostic_orphaned": len(agnostic - parented),
        },
    )


def company_alignment_check(database: UnitOfWorkFactory) -> Check:
    async def evaluate(context: CheckContext) -> CheckResult:
        agnostic_versions = {
            concept.id: version.id
            for concept, version in current(context)
            if knowledge_level(version.candidate) is KnowledgeLevel.PLATFORM_AGNOSTIC
        }
        source_ids: dict[UUID, set[UUID]] = {}
        async with database() as uow:
            for concept_id in sorted(agnostic_versions, key=str):
                source_ids[concept_id] = {
                    record.source.item.id
                    for record in await uow.knowledge.provenance(concept_id)
                    if record.occurrence.concept_version_id == agnostic_versions[concept_id]
                    and record.source.revision_id == record.revision.id
                }
        return await company_alignment(context, source_ids)

    return Check("company_alignment", evaluate, "3")


def _mapping_key(
    mapper: TraceabilityMapper,
    child: TraceabilityAssertion,
    parents: tuple[TraceabilityAssertion, ...],
) -> str:
    return fingerprint(
        kind="control-traceability-v1",
        provider=mapper.fingerprint,
        child=child.model_dump(mode="json"),
        parents=[parent.model_dump(mode="json") for parent in parents],
    )


async def _map_control(
    database: UnitOfWorkFactory,
    mapper: TraceabilityMapper,
    child: TraceabilityAssertion,
    parents: tuple[TraceabilityAssertion, ...],
    cache: dict[str, SemanticCacheEntry],
) -> TraceabilityVerification:
    key = _mapping_key(mapper, child, parents)
    entry = cache.get(key)
    if isinstance(entry, CachedTraceability):
        return TraceabilityVerification(
            selection=entry.selection,
            method=entry.method,
            model_id=entry.model_id,
        )
    result = TraceabilityVerification.model_validate(
        (await mapper.map(child, parents)).model_dump()
    )
    if not result.completed:
        raise ValueError("Traceability mapping incomplete; resume after provider recovery")
    saved = CachedTraceability(
        selection=result.selection,
        method=result.method,
        model_id=result.model_id,
    )
    async with database() as uow:
        await uow.workflow.save_semantic_cache({key: saved})
        await uow.commit()
    return result


def _rank_parents(
    child: TraceabilityAssertion,
    parents: list[TraceabilityAssertion],
    vectors: dict[str, tuple[float, ...]],
    limit: int,
) -> tuple[TraceabilityAssertion, ...]:
    if vectors:
        child_vector = vectors[child.statement]
        return tuple(
            parent
            for _, parent in sorted(
                ((cosine(child_vector, vectors[parent.statement]), parent) for parent in parents),
                key=lambda item: (-item[0], str(item[1].concept_id)),
            )[:limit]
        )
    return tuple(
        parent
        for _, parent in sorted(
            (
                (
                    SequenceMatcher(
                        None,
                        child.statement.casefold(),
                        parent.statement.casefold(),
                        autojunk=True,
                    ).ratio(),
                    parent,
                )
                for parent in parents
            ),
            key=lambda item: (-item[0], str(item[1].concept_id)),
        )[:limit]
    )


def _semantic(assertion: TraceabilityAssertion) -> SemanticAssertion:
    return SemanticAssertion(
        occurrence_id=assertion.occurrence_id,
        statement=assertion.statement,
        excerpt=assertion.excerpt,
        semantic_type=assertion.semantic_type,
        scope=assertion.scope,
    )


def platform_alignment_check(
    database: UnitOfWorkFactory,
    mapper: TraceabilityMapper | None,
    embedding_provider: EmbeddingPort | None,
    *,
    concurrency: int,
) -> Check:
    version = fingerprint(
        implementation="platform-alignment-v2",
        mapper=mapper.fingerprint if mapper else None,
        embeddings=embedding_provider.fingerprint if embedding_provider else None,
    )

    async def evaluate(context: CheckContext) -> CheckResult:
        configuration = ControlMappingOptions.model_validate(context.options.parameters)
        entries = _entries(context)
        parents = [
            assertion(concept, concept_version)
            for concept, concept_version in entries.values()
            if concept_version.candidate.semantic_type in _AGNOSTIC_REQUIREMENTS
        ]
        controls = [
            assertion(concept, concept_version)
            for concept, concept_version in entries.values()
            if concept_version.candidate.semantic_type is SemanticType.PLATFORM_REQUIREMENT
        ]
        parent_ids = {parent.concept_id for parent in parents}
        edges = relationship_edges(context, exclude_checks=frozenset({"platform_alignment"}))

        def current_parents(control_id: UUID) -> set[UUID]:
            return {
                edge.target
                for edge in edges
                if edge.source == control_id
                and edge.target in parent_ids
                and edge.kind is RelationshipKind.REFINES
            }

        unmapped = [
            control
            for control in sorted(controls, key=lambda value: str(value.concept_id))
            if not current_parents(control.concept_id)
        ]
        scanned = unmapped[: context.options.max_candidates]
        texts = list(dict.fromkeys(assertion.statement for assertion in (*parents, *scanned)))
        vectors: dict[str, tuple[float, ...]] = {}
        embedding_calls = embedding_tokens = 0
        costs: list[float | None] = []
        if embedding_provider and texts:
            vectors, embedding_calls, embedding_tokens, costs = await _embeddings(
                database, embedding_provider, texts
            )
        ranked = {
            child.concept_id: _rank_parents(
                child,
                parents,
                vectors,
                context.options.embedding_neighbors,
            )
            for child in scanned
        }
        mapping_cache: dict[str, SemanticCacheEntry] = {}
        active_mapper = mapper
        if active_mapper:
            keys = [
                _mapping_key(active_mapper, child, ranked[child.concept_id])
                for child in scanned
                if ranked[child.concept_id]
            ]
            if keys:
                async with database() as uow:
                    mapping_cache = await uow.workflow.semantic_cache(keys)

        mapped: list[tuple[TraceabilityAssertion, TraceabilityVerification]] = []
        if active_mapper:
            queue: asyncio.Queue[TraceabilityAssertion | None] = asyncio.Queue(
                maxsize=max(concurrency * 2, 1)
            )

            async def produce() -> None:
                for child in scanned:
                    await queue.put(child)
                for _ in range(concurrency):
                    await queue.put(None)

            async def consume() -> None:
                while (child := await queue.get()) is not None:
                    candidates = ranked[child.concept_id]
                    if candidates:
                        mapped.append(
                            (
                                child,
                                await _map_control(
                                    database,
                                    active_mapper,
                                    child,
                                    candidates,
                                    mapping_cache,
                                ),
                            )
                        )

            async with asyncio.TaskGroup() as group:
                group.create_task(produce())
                for _ in range(concurrency):
                    group.create_task(consume())

        by_parent = {parent.concept_id: parent for parent in parents}
        results: list[Finding] = []
        model_calls = output_tokens = latency = validation_failures = 0
        input_tokens = embedding_tokens
        selections = rejected = unknown = 0
        for child, mapped_result in mapped:
            model_calls += mapped_result.model_calls
            input_tokens += mapped_result.usage.input_tokens
            output_tokens += mapped_result.usage.output_tokens
            latency += mapped_result.usage.latency_ms
            validation_failures += mapped_result.usage.validation_failures
            if mapped_result.model_calls:
                costs.append(mapped_result.usage.cost)
            retained = sorted(
                (
                    link
                    for link in mapped_result.selection.links
                    if link.confidence >= configuration.confidence_threshold
                ),
                key=lambda link: (-link.confidence, str(link.parent_concept_id)),
            )[: configuration.max_parents]
            rejected += len(mapped_result.selection.links) - len(retained)
            if not retained:
                unknown += 1
            for link in retained:
                parent = by_parent[link.parent_concept_id]
                selections += 1
                result = finding(
                    context,
                    "platform_alignment",
                    version,
                    action="relationship",
                    concept_ids=(child.concept_id, parent.concept_id),
                    version_ids=(child.version_id, parent.version_id),
                    occurrence_ids=(child.occurrence_id, parent.occurrence_id),
                    relationship=RelationshipKind.REFINES,
                    scope=child.scope,
                    confidence=link.confidence,
                    explanation=link.rationale,
                    uncertainty=(
                        "Model-inferred traceability is reviewable and does not grant authority."
                    ),
                    proposed_by=mapped_result.model_id or "semantic-verifier",
                )
                results.append(result)
                edges.add(TraceEdge(child.concept_id, parent.concept_id, RelationshipKind.REFINES))

        parent_counts = {
            control.concept_id: len(current_parents(control.concept_id)) for control in controls
        }
        agnostic_with_controls = {
            parent_id for control_id in parent_counts for parent_id in current_parents(control_id)
        }
        return CheckResult(
            findings=tuple(results),
            candidates=sum(len(value) for value in ranked.values()),
            model_calls=model_calls,
            embedding_calls=embedding_calls,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency,
                cost=(
                    sum(value for value in costs if value is not None)
                    if costs and all(value is not None for value in costs)
                    else None
                ),
                validation_failures=validation_failures,
            ),
            statistics={
                "platform_controls_total": len(controls),
                "platform_controls_parented": sum(count > 0 for count in parent_counts.values()),
                "platform_controls_orphaned": sum(count == 0 for count in parent_counts.values()),
                "platform_controls_multi_parented": sum(
                    count > 1 for count in parent_counts.values()
                ),
                "platform_controls_unscanned": max(len(unmapped) - len(scanned), 0),
                "agnostic_requirements_total": len(parents),
                "agnostic_with_controls": len(agnostic_with_controls),
                "agnostic_without_controls": len(parent_ids - agnostic_with_controls),
                "parent_candidates_compared": sum(len(value) for value in ranked.values()),
                "parent_selections": selections,
                "parent_selections_below_threshold": rejected,
                "parent_selections_unknown": unknown,
            },
        )

    return Check("platform_alignment", evaluate, version, ControlMappingOptions)


def sibling_comparison_check(
    database: UnitOfWorkFactory,
    verifier: SemanticVerifier | None,
    embedding_provider: EmbeddingPort | None,
    *,
    concurrency: int,
) -> Check:
    version = fingerprint(
        implementation="sibling-control-comparison-v2",
        verifier=verifier.fingerprint if verifier else None,
        embeddings=embedding_provider.fingerprint if embedding_provider else None,
    )

    async def evaluate(context: CheckContext) -> CheckResult:
        entries = _entries(context)
        parent_ids = {
            concept_id
            for concept_id, (_, concept_version) in entries.items()
            if concept_version.candidate.semantic_type in _AGNOSTIC_REQUIREMENTS
        }
        control_ids = {
            concept_id
            for concept_id, (_, concept_version) in entries.items()
            if concept_version.candidate.semantic_type is SemanticType.PLATFORM_REQUIREMENT
        }
        groups: dict[UUID, set[UUID]] = defaultdict(set)
        for edge in relationship_edges(
            context, exclude_checks=frozenset({"sibling_control_comparison"})
        ):
            if (
                edge.kind is RelationshipKind.REFINES
                and edge.source in control_ids
                and edge.target in parent_ids
            ):
                groups[edge.target].add(edge.source)
        eligible = sum(len(group) * (len(group) - 1) // 2 for group in groups.values())
        assertions = {
            concept_id: assertion(*entries[concept_id])
            for concept_id in {item for group in groups.values() for item in group}
        }
        texts = list(dict.fromkeys(item.statement for item in assertions.values()))
        vectors: dict[str, tuple[float, ...]] = {}
        embedding_calls = embedding_tokens = 0
        costs: list[float | None] = []
        if embedding_provider and texts:
            vectors, embedding_calls, embedding_tokens, costs = await _embeddings(
                database, embedding_provider, texts
            )
        ranked_pairs: dict[tuple[UUID, UUID], float] = {}
        for group in groups.values():
            for left_id, right_id in combinations(sorted(group, key=str), 2):
                left, right = assertions[left_id], assertions[right_id]
                score = (
                    cosine(vectors[left.statement], vectors[right.statement])
                    if vectors
                    else SequenceMatcher(
                        None,
                        left.statement.casefold(),
                        right.statement.casefold(),
                        autojunk=True,
                    ).ratio()
                )
                pair = (left_id, right_id)
                ranked_pairs[pair] = max(score, ranked_pairs.get(pair, 0))
        selected_pairs = [
            pair
            for pair, _ in sorted(
                ranked_pairs.items(),
                key=lambda item: (-item[1], str(item[0][0]), str(item[0][1])),
            )[: context.options.max_candidates]
        ]
        cache: dict[str, SemanticCacheEntry] = {}
        if verifier:
            from ekb.stages.semantic import _verification_key

            keys = [
                _verification_key(
                    verifier,
                    _semantic(assertions[left_id]),
                    _semantic(assertions[right_id]),
                )
                for left_id, right_id in selected_pairs
            ]
            if keys:
                async with database() as uow:
                    cache = await uow.workflow.semantic_cache(keys)

        compared: list[tuple[UUID, UUID, Verification]] = []
        active_verifier = verifier
        if active_verifier:
            queue: asyncio.Queue[tuple[UUID, UUID] | None] = asyncio.Queue(
                maxsize=max(concurrency * 2, 1)
            )

            async def produce() -> None:
                for pair in selected_pairs:
                    await queue.put(pair)
                for _ in range(concurrency):
                    await queue.put(None)

            async def consume() -> None:
                while (pair := await queue.get()) is not None:
                    left_id, right_id = pair
                    left = _semantic(assertions[left_id])
                    right = _semantic(assertions[right_id])
                    compared.append(
                        (
                            left_id,
                            right_id,
                            await _verification(database, active_verifier, left, right, cache),
                        )
                    )

            async with asyncio.TaskGroup() as group:
                group.create_task(produce())
                for _ in range(concurrency):
                    group.create_task(consume())

        results: list[Finding] = []
        counts = defaultdict(int)
        model_calls = output_tokens = latency = validation_failures = 0
        input_tokens = embedding_tokens
        for left_id, right_id, raw_verification in compared:
            semantic = raw_verification
            model_calls += semantic.model_calls
            input_tokens += semantic.usage.input_tokens
            output_tokens += semantic.usage.output_tokens
            latency += semantic.usage.latency_ms
            validation_failures += semantic.usage.validation_failures
            if semantic.model_calls:
                costs.append(semantic.usage.cost)
            outcome = semantic.judgment.outcome
            counts[outcome] += 1
            if outcome == "unknown":
                continue
            left, right = assertions[left_id], assertions[right_id]
            action: Literal["alias", "keep_distinct", "relationship"]
            relationship: ConceptRelationshipKind | None = None
            if outcome in {"exact_duplicate", "equivalent"}:
                action = "alias"
            elif outcome in {"compatible_extension", "more_specific_guidance"}:
                action, relationship = "relationship", RelationshipKind.MAPS_TO
            elif outcome == "potential_conflict":
                action, relationship = "relationship", RelationshipKind.TENSION_WITH
            else:
                action = "keep_distinct"
            results.append(
                finding(
                    context,
                    "sibling_control_comparison",
                    version,
                    action=action,
                    concept_ids=(left_id, right_id),
                    version_ids=(left.version_id, right.version_id),
                    occurrence_ids=(left.occurrence_id, right.occurrence_id),
                    relationship=relationship,
                    scope=left.scope,
                    confidence=semantic.judgment.confidence,
                    explanation=semantic.judgment.rationale,
                    uncertainty=(
                        "Sibling comparison is review-only; adjacency does not imply identity."
                    ),
                    proposed_by=semantic.model_id or "semantic-verifier",
                )
            )
        return CheckResult(
            findings=tuple(results),
            candidates=len(selected_pairs),
            model_calls=model_calls,
            embedding_calls=embedding_calls,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency,
                cost=(
                    sum(value for value in costs if value is not None)
                    if costs and all(value is not None for value in costs)
                    else None
                ),
                validation_failures=validation_failures,
            ),
            statistics={
                "sibling_pairs_eligible": eligible,
                "sibling_pairs_compared": len(compared),
                "sibling_pairs_duplicate": (counts["exact_duplicate"] + counts["equivalent"]),
                "sibling_pairs_adjacent": (
                    counts["compatible_extension"] + counts["more_specific_guidance"]
                ),
                "sibling_pairs_distinct": counts["distinct"],
                "sibling_pairs_conflicting": counts["potential_conflict"],
                "sibling_pairs_unknown": counts["unknown"],
                "sibling_pairs_unscanned": max(eligible - len(selected_pairs), 0),
            },
        )

    return Check("sibling_control_comparison", evaluate, version)
