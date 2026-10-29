from __future__ import annotations

import asyncio

from ekb.domain.benchmarks import BenchmarkControl, BenchmarkPassage
from ekb.domain.models import RelationshipKind, Usage, fingerprint
from ekb.domain.reconciliation import ControlView
from ekb.observability import tracer
from ekb.ports import UnitOfWorkFactory
from ekb.ports.workflow import CheckContext, CheckResult
from ekb.services.reconciliation import BenchmarkService
from ekb.services.retrieval import cosine, vector_pairs
from ekb.stages.enrichment import Check, current, evidence, finding, pairs, semantic_duplicates


def semantic_check(database: UnitOfWorkFactory, service: BenchmarkService) -> Check:
    provider_version = fingerprint(
        implementation="generic-semantic-v2",
        verifier=service.verifier.fingerprint if service.verifier else None,
        embeddings=service.embeddings.fingerprint if service.embeddings else None,
    )

    async def evaluate(context: CheckContext) -> CheckResult:
        if service.verifier is None and service.embeddings is None:
            return await semantic_duplicates(context)
        candidate_context = context
        if service.embeddings:
            candidate_context = context.model_copy(
                update={
                    "options": context.options.model_copy(update={"similarity_threshold": 0.35})
                }
            )
        candidates = await asyncio.to_thread(lambda: list(pairs(candidate_context)))
        pool = (
            sorted(current(context), key=lambda cv: str(cv[1].id))[
                : context.options.embedding_pool_size
            ]
            if service.embeddings
            else []
        )
        assertions = {v.id: (c, v) for c, v in pool}
        for left, old, right, new, _ in candidates:
            assertions.update({old.id: (left, old), new.id: (right, new)})
        views = {}
        # This projection reuses the grounded verifier, never creating synthetic benchmark records.
        async with database() as uow:
            for concept, version in assertions.values():
                if version.id in views:
                    continue
                provenance = await uow.knowledge.provenance(concept.id)
                source = next(
                    (p for p in provenance if p.occurrence.concept_version_id == version.id),
                    None,
                )
                if source is None:
                    continue
                occurrence = source.occurrence
                passage = BenchmarkPassage(
                    source_id=source.revision.source_id,
                    source_url=source.revision.source_location or source.source.item.location,
                    content_hash=source.revision.content_hash,
                    section="Source assertion",
                    excerpt=occurrence.excerpt,
                    start=occurrence.start,
                    end=occurrence.end,
                )
                views[version.id] = ControlView(
                    snapshot_id=occurrence.id,
                    revision_id=source.revision.id,
                    segment_id=source.segment.id,
                    retrieved_at=source.revision.recorded_at,
                    scope=version.candidate.scope,
                    control=BenchmarkControl(
                        benchmark="source-assertion",
                        edition="knowledge",
                        publisher_version=version.publication.publisher_version or "",
                        domain=version.candidate.kind,
                        control_id=str(version.id),
                        title=version.candidate.statement,
                        security_principle=occurrence.excerpt,
                        evidence=passage,
                    ),
                )
        vectors, embedding_calls, embedding_tokens = await service.assertion_vectors(
            [views[v.id] for _, v in pool if v.id in views]
        )
        if service.embeddings:
            by_version = {
                v.id: vectors[("knowledge", str(v.id))]
                for _, v in pool
                if ("knowledge", str(v.id)) in vectors
            }
            retrieved = await asyncio.to_thread(
                vector_pairs,
                by_version,
                context.options.embedding_neighbors,
                context.options.max_candidates,
            )
            unique = {
                tuple(sorted((old.id, new.id), key=str)): (left, old, right, new, score)
                for left, old, right, new, score in candidates
            }
            for old_id, new_id, score in retrieved:
                left, old = assertions[old_id]
                right, new = assertions[new_id]
                if left.id != right.id and old.candidate.kind == new.candidate.kind:
                    key = (old_id, new_id)
                    if key not in unique:
                        unique[key] = (left, old, right, new, score)
            candidates = sorted(unique.values(), key=lambda c: (-c[4], str(c[1].id), str(c[3].id)))[
                : context.options.max_candidates
            ]

        def rank():
            ranked = []
            for candidate in candidates:
                _, old, _, new, structural = candidate
                score = structural
                a = vectors.get(("knowledge", str(old.id)))
                b = vectors.get(("knowledge", str(new.id)))
                if a and b:
                    if len(a) != len(b):
                        raise ValueError("Embedding provider changed vector dimensions")
                    score = max(
                        score,
                        cosine(a, b),
                    )
                if score >= context.options.similarity_threshold:
                    ranked.append((score, candidate))
            return sorted(ranked, key=lambda x: -x[0])

        ranked = await asyncio.to_thread(rank)
        results, calls, input_tokens, output_tokens, latency = [], 0, embedding_tokens, 0, 0.0
        cost: float | None = None
        for score, (left, old, right, new, _) in ranked:
            if old.candidate.scope != new.candidate.scope:
                continue
            if old.id not in views or new.id not in views:
                continue
            action, relation = "alias", None
            confidence, explanation = min(score, 0.85), "Embedding/scoped retrieval candidate."
            if service.verifier:
                with tracer.start_as_current_span("enrichment.semantic_comparison"):
                    verification = await service.verify_assertions(views[old.id], views[new.id])
                if verification.method not in {"small_model", "structural_rules"}:
                    raise ValueError(
                        "Semantic verification incomplete; resume after provider recovery"
                    )
                calls += verification.model_calls
                input_tokens += verification.usage.input_tokens
                output_tokens += verification.usage.output_tokens
                latency += verification.usage.latency_ms
                if verification.usage.cost is not None:
                    cost = (cost or 0) + verification.usage.cost
                confidence = verification.judgment.confidence
                explanation = verification.judgment.rationale
                outcome = verification.judgment.outcome
                if outcome == "distinct":
                    action = "keep_distinct"
                elif outcome in {"compatible_extension", "more_specific_guidance", "unknown"}:
                    action, relation = "relationship", RelationshipKind.OVERLAPS_WITH
                elif outcome == "potential_conflict":
                    action, relation = "relationship", RelationshipKind.TENSION_WITH
            results.append(
                finding(
                    context,
                    "semantic_duplicate",
                    provider_version,
                    action=action,
                    concept_ids=(left.id, right.id),
                    version_ids=(old.id, new.id),
                    occurrence_ids=evidence(left, old) + evidence(right, new),
                    relationship=relation,
                    scope=old.candidate.scope,
                    confidence=confidence,
                    explanation=explanation,
                    uncertainty="Review-only interpretation, not a merge, derivation or authority.",
                )
            )
        return CheckResult(
            findings=tuple(results),
            candidates=len(candidates),
            model_calls=calls,
            embedding_calls=embedding_calls,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency,
                cost=cost,
            ),
        )

    return Check("semantic_duplicate", evaluate, provider_version)
