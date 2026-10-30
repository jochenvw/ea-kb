from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator
from difflib import SequenceMatcher

from ekb.domain.execution import CheckOptions, ProcessorSpec
from ekb.domain.models import (
    ConceptDetail,
    ConceptVersion,
    Model,
    RelationshipKind,
    fingerprint,
    stable_id,
)
from ekb.domain.resolution import Finding, overlaps
from ekb.observability import tracer
from ekb.ports import EmbeddingPort, SemanticVerifier, UnitOfWorkFactory
from ekb.ports.workflow import CheckContext, CheckResult, KnowledgeCheck
from ekb.stages import CheckedStage


class Check:
    def __init__(
        self,
        identifier: str,
        evaluate: Callable[[CheckContext], Awaitable[CheckResult]],
        version: str = "1",
        configuration_type: type[Model] = Model,
    ):
        self.spec = ProcessorSpec(
            id=identifier,
            name=identifier.replace("_", " "),
            version=version,
            configuration_schema=configuration_type.model_json_schema(),
        )
        self._evaluate = evaluate
        self.configuration_type = configuration_type

    def validate_configuration(self, options: CheckOptions) -> None:
        self.configuration_type.model_validate(options.parameters)

    async def evaluate(self, context: CheckContext) -> CheckResult:
        return await self._evaluate(context)


def current(context: CheckContext) -> list[tuple[ConceptDetail, ConceptVersion]]:
    return [
        (c, v)
        for c in context.snapshot.concepts
        for v in c.versions
        if v.id in context.snapshot.current_version_ids
    ]


def pairs(
    context: CheckContext,
) -> Iterator[tuple[ConceptDetail, ConceptVersion, ConceptDetail, ConceptVersion, float]]:
    with tracer.start_as_current_span("enrichment.candidate_retrieval"):
        entries = sorted(current(context), key=lambda cv: str(cv[1].id))
        index: dict[str, set[int]] = defaultdict(set)
        count = 0
        tokens: list[set[str]] = []
        for i, (concept, version) in enumerate(entries):
            words = set(re.findall(r"\b[a-z0-9-]{4,}\b", version.candidate.statement.casefold()))
            matches = sorted(
                {j for word in words for j in index[word]},
                key=lambda j: (-len(words & tokens[j]) / max(len(words | tokens[j]), 1), j),
            )[:20]
            ranked = []
            for j in matches:
                other, previous = entries[j]
                if (
                    concept.id == other.id
                    or version.candidate.semantic_type != previous.candidate.semantic_type
                ):
                    continue
                if count >= context.options.max_candidates:
                    return
                count += 1
                score = SequenceMatcher(
                    None,
                    version.candidate.statement.casefold(),
                    previous.candidate.statement.casefold(),
                    autojunk=True,
                ).ratio()
                if score >= context.options.similarity_threshold:
                    ranked.append((score, other, previous))
            for score, other, previous in sorted(ranked, key=lambda x: (-x[0], str(x[1].id))):
                yield other, previous, concept, version, score
            for word in words:
                index[word].add(i)
            tokens.append(words)


def finding(
    context: CheckContext,
    identifier: str,
    version: str,
    **values,
) -> Finding:
    evidence = {
        "concepts": values.get("concept_ids"),
        "versions": values.get("version_ids"),
        "occurrences": values.get("occurrence_ids"),
        "action": values.get("action"),
        "relationship": values.get("relationship"),
        "scope": values.get("scope"),
        "change": values.get("proposed_change"),
    }
    return Finding(
        id=stable_id(identifier, fingerprint(**evidence)),
        check_id=identifier,
        check_version=context.check_version,
        input_revision=context.input_revision,
        run_id=context.run_id,
        **values,
    )


def evidence(concept: ConceptDetail, version: ConceptVersion) -> tuple:
    return tuple(o.id for o in concept.occurrences if o.concept_version_id == version.id)


async def exact_duplicates(context: CheckContext) -> CheckResult:
    findings = []
    for concept, version in current(context):
        refs = evidence(concept, version)
        if len(refs) > 1:
            findings.append(
                finding(
                    context,
                    "exact_duplicate",
                    "1",
                    action="recognize_identity",
                    concept_ids=(concept.id,),
                    version_ids=(version.id,),
                    occurrence_ids=refs,
                    confidence=1.0,
                    deterministic=True,
                    explanation="Exact content, modality and scope share one stable identity; "
                    "separate source occurrences are retained.",
                )
            )
    return CheckResult(findings=tuple(findings), candidates=len(findings))


async def semantic_duplicates(context: CheckContext) -> CheckResult:
    results = []
    candidates = await asyncio.to_thread(lambda: list(pairs(context)))
    for left, old, right, new, score in candidates:
        if not overlaps(old.candidate.scope, new.candidate.scope):
            continue

        def negative(text: str) -> bool:
            return bool(re.search(r"\b(?:not|never|prohibited|forbidden)\b", text, re.I))

        conflict = negative(old.candidate.statement) != negative(new.candidate.statement)
        with tracer.start_as_current_span("enrichment.semantic_comparison"):
            results.append(
                finding(
                    context,
                    "semantic_duplicate",
                    "1",
                    action="relationship" if conflict else "alias",
                    concept_ids=(left.id, right.id),
                    version_ids=(old.id, new.id),
                    occurrence_ids=evidence(left, old) + evidence(right, new),
                    relationship=RelationshipKind.TENSION_WITH if conflict else None,
                    confidence=min(score, 0.85),
                    scope=old.candidate.scope,
                    explanation="Similar scoped source assertions with different polarity; review "
                    "whether this is a trade-off or contradiction."
                    if conflict
                    else "Scoped lexical retrieval proposes a possible equivalent or duplicate.",
                    uncertainty="Similarity does not establish identity or authority.",
                )
            )
    return CheckResult(findings=tuple(results), candidates=len(results))


async def scope_authority(context: CheckContext) -> CheckResult:
    results = []
    candidates = await asyncio.to_thread(lambda: list(pairs(context)))
    for left, old, right, new, score in candidates:
        if old.candidate.scope != new.candidate.scope:
            results.append(
                finding(
                    context,
                    "scope_authority",
                    "1",
                    action="keep_distinct",
                    concept_ids=(left.id, right.id),
                    version_ids=(old.id, new.id),
                    occurrence_ids=evidence(left, old) + evidence(right, new),
                    confidence=score,
                    explanation="Similar assertions have different applicability; scopes are "
                    + (
                        "overlapping."
                        if overlaps(old.candidate.scope, new.candidate.scope)
                        else "disjoint, not a contradiction."
                    ),
                    uncertainty="Maturity, modality and authority are independent of similarity.",
                )
            )
    return CheckResult(findings=tuple(results), candidates=len(results))


async def lineage(context: CheckContext) -> CheckResult:
    results, seen = [], set()
    concepts = {c.id: c for c in context.snapshot.concepts}
    for concept in concepts.values():
        for relationship in concept.relationships:
            if relationship.id in seen:
                continue
            seen.add(relationship.id)
            results.append(
                finding(
                    context,
                    "lineage",
                    "1",
                    action="relationship",
                    concept_ids=(relationship.from_concept_id, relationship.to_concept_id),
                    occurrence_ids=(relationship.occurrence_id,),
                    relationship=relationship.kind,
                    scope=relationship.scope,
                    confidence=0.7,
                    explanation="Source extraction proposed this evidenced relationship; it is not "
                    "a published or accepted relationship without review.",
                )
            )
        for version in concept.versions:
            if version.predecessor_id:
                previous = next(
                    (v for v in concept.versions if v.id == version.predecessor_id), None
                )
                if previous and concept.occurrences:
                    results.append(
                        finding(
                            context,
                            "lineage",
                            "1",
                            action="version",
                            concept_ids=(concept.id,),
                            version_ids=(version.id, previous.id),
                            occurrence_ids=tuple(o.id for o in concept.occurrences),
                            confidence=1.0,
                            deterministic=True,
                            explanation="Explicit persisted concept-version predecessor, not a "
                            "semantic change inferred from a changed source hash.",
                        )
                    )
    return CheckResult(findings=tuple(results), candidates=len(results))


def enrichment_stage(
    database: UnitOfWorkFactory,
    verifier: SemanticVerifier | None = None,
    embeddings: EmbeddingPort | None = None,
) -> CheckedStage:
    from ekb.stages.semantic import semantic_check

    checks: tuple[KnowledgeCheck, ...] = (
        Check("exact_duplicate", exact_duplicates),
        semantic_check(database, verifier, embeddings),
        Check("lineage", lineage),
        Check("scope_authority", scope_authority),
    )
    return CheckedStage(
        database,
        ProcessorSpec(
            id="enrichment",
            name="Semantic enrichment",
            version="1",
            dependencies=("indexing",),
            input_contract="indexed/v1",
            output_contract="enriched/v1",
        ),
        checks,
    )
