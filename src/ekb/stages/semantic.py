from __future__ import annotations

import asyncio
from uuid import UUID

from pydantic import ValidationError

from ekb.domain.models import RelationshipKind, Usage, fingerprint
from ekb.domain.reconciliation import (
    CachedEmbedding,
    CachedVerification,
    EmbeddingResult,
    SemanticAssertion,
    SemanticCacheEntry,
    SemanticJudgment,
    Verification,
    semantic_input,
    structural_verification,
)
from ekb.observability import tracer
from ekb.ports import EmbeddingPort, SemanticVerifier, UnitOfWorkFactory
from ekb.ports.workflow import CheckContext, CheckResult
from ekb.services.retrieval import cosine, vector_pairs
from ekb.stages.enrichment import Check, current, evidence, finding, pairs, semantic_duplicates


async def _embeddings(
    database: UnitOfWorkFactory, provider: EmbeddingPort, texts: list[str]
) -> tuple[dict[str, tuple[float, ...]], int, int, list[float | None]]:
    if not texts:
        return {}, 0, 0, []
    keys = {
        text: fingerprint(kind="semantic-embedding-v1", provider=provider.fingerprint, text=text)
        for text in texts
    }
    async with database() as uow:
        cached = await uow.workflow.semantic_cache(list(keys.values()))
    vectors: dict[str, tuple[float, ...]] = {}
    for text, key in keys.items():
        entry = cached.get(key)
        if isinstance(entry, CachedEmbedding):
            try:
                vectors[text] = CachedEmbedding.model_validate(entry.model_dump()).vector
            except ValidationError:
                continue
    if len({len(vector) for vector in vectors.values()}) > 1:
        vectors.clear()

    calls = tokens = 0
    costs: list[float | None] = []

    async def request(batch: list[str]) -> dict[str, CachedEmbedding]:
        nonlocal calls, tokens
        result = EmbeddingResult.model_validate((await provider.embed(batch)).model_dump())
        if len(result.vectors) != len(batch):
            raise ValueError("Embedding provider returned the wrong number of vectors")
        try:
            entries = {
                text: CachedEmbedding(vector=vector)
                for text, vector in zip(batch, result.vectors, strict=True)
            }
        except ValidationError as error:
            raise ValueError("Embedding provider returned invalid vectors") from error
        if len({len(entry.vector) for entry in entries.values()}) != 1:
            raise ValueError("Embedding provider returned inconsistent vector dimensions")
        calls += result.calls
        tokens += result.input_tokens
        if result.calls:
            costs.append(
                result.cost if result.usage_complete is True and result.input_tokens > 0 else None
            )
        return entries

    missing = [text for text in texts if text not in vectors]
    if missing:
        fresh = await request(missing)
        dimensions = len(next(iter(fresh.values())).vector)
        mismatched = [text for text, vector in vectors.items() if len(vector) != dimensions]
        if mismatched:
            replacements = await request(mismatched)
            if any(len(entry.vector) != dimensions for entry in replacements.values()):
                raise ValueError("Embedding provider changed vector dimensions")
            fresh.update(replacements)
        vectors.update({text: entry.vector for text, entry in fresh.items()})
        async with database() as uow:
            await uow.workflow.save_semantic_cache(
                {keys[text]: entry for text, entry in fresh.items()}
            )
            await uow.commit()
    return vectors, calls, tokens, costs


def _verification_key(
    provider: SemanticVerifier, old: SemanticAssertion, new: SemanticAssertion
) -> str:
    return fingerprint(
        kind="semantic-verification-v1",
        provider=provider.fingerprint,
        old=semantic_input(old),
        new=semantic_input(new),
    )


def _grounded(judgment: SemanticJudgment, old: SemanticAssertion, new: SemanticAssertion) -> bool:
    return judgment.old_excerpt in old.excerpt and judgment.new_excerpt in new.excerpt


async def _verification(
    database: UnitOfWorkFactory,
    provider: SemanticVerifier,
    old: SemanticAssertion,
    new: SemanticAssertion,
    cache: dict[str, SemanticCacheEntry],
) -> Verification:
    baseline = structural_verification(old, new)
    if baseline.judgment.outcome == "exact_duplicate" or semantic_input(old) == semantic_input(new):
        return baseline
    key = _verification_key(provider, old, new)
    entry = cache.get(key)
    if isinstance(entry, CachedVerification):
        try:
            valid = CachedVerification.model_validate(entry.model_dump())
        except ValidationError:
            pass
        else:
            if _grounded(valid.judgment, old, new):
                return Verification(
                    judgment=valid.judgment,
                    method=valid.method,
                    status=valid.status,
                    model_id=valid.model_id,
                )
    verification = Verification.model_validate((await provider.verify(old, new)).model_dump())
    if not verification.completed:
        raise ValueError("Semantic verification incomplete; resume after provider recovery")
    if not _grounded(verification.judgment, old, new):
        raise ValueError("Semantic verifier returned ungrounded evidence")
    saved = CachedVerification(
        judgment=verification.judgment,
        method=verification.method,
        model_id=verification.model_id,
    )
    async with database() as uow:
        await uow.workflow.save_semantic_cache({key: saved})
        await uow.commit()
    cache[key] = saved
    return verification


def semantic_check(
    database: UnitOfWorkFactory,
    verifier: SemanticVerifier | None,
    embeddings: EmbeddingPort | None,
) -> Check:
    provider_version = fingerprint(
        implementation="generic-semantic-v4",
        verifier=verifier.fingerprint if verifier else None,
        embeddings=embeddings.fingerprint if embeddings else None,
    )

    async def evaluate(context: CheckContext) -> CheckResult:
        if verifier is None and embeddings is None:
            return await semantic_duplicates(context)
        candidate_context = context
        if embeddings:
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
            if embeddings
            else []
        )
        assertions = {v.id: (c, v) for c, v in pool}
        for left, old, right, new, _ in candidates:
            assertions.update({old.id: (left, old), new.id: (right, new)})

        views: dict[UUID, SemanticAssertion] = {}
        async with database() as uow:
            for concept, version in assertions.values():
                provenance = await uow.knowledge.provenance(concept.id)
                source = next(
                    (p for p in provenance if p.occurrence.concept_version_id == version.id),
                    None,
                )
                if source is not None:
                    views[version.id] = SemanticAssertion(
                        occurrence_id=source.occurrence.id,
                        statement=version.candidate.statement,
                        excerpt=source.occurrence.excerpt,
                        semantic_type=version.candidate.semantic_type,
                        scope=version.candidate.scope,
                    )

        vectors: dict[UUID, tuple[float, ...]] = {}
        embedding_calls = embedding_tokens = 0
        costs: list[float | None] = []
        if embeddings:
            texts = list(dict.fromkeys(views[v.id].statement for _, v in pool if v.id in views))
            by_text, embedding_calls, embedding_tokens, costs = await _embeddings(
                database, embeddings, texts
            )
            vectors = {v.id: by_text[views[v.id].statement] for _, v in pool if v.id in views}
            retrieved = await asyncio.to_thread(
                vector_pairs,
                vectors,
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
                if (
                    left.id != right.id
                    and old.candidate.semantic_type == new.candidate.semantic_type
                ):
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
                a, b = vectors.get(old.id), vectors.get(new.id)
                if a and b:
                    score = max(score, cosine(a, b))
                if score >= context.options.similarity_threshold:
                    ranked.append((score, candidate))
            return sorted(ranked, key=lambda x: -x[0])

        ranked = await asyncio.to_thread(rank)
        verification_cache: dict[str, SemanticCacheEntry] = {}
        if verifier:
            keys = [
                _verification_key(verifier, views[old.id], views[new.id])
                for _, (_, old, _, new, _) in ranked
                if old.candidate.scope == new.candidate.scope
                and old.id in views
                and new.id in views
            ]
            if keys:
                async with database() as uow:
                    verification_cache = await uow.workflow.semantic_cache(keys)
        results, calls, input_tokens, output_tokens, latency = [], 0, embedding_tokens, 0, 0.0
        validation_failures = 0
        for score, (left, old, right, new, _) in ranked:
            if old.candidate.scope != new.candidate.scope:
                continue
            if old.id not in views or new.id not in views:
                continue
            action, relation = "alias", None
            confidence, explanation = min(score, 0.85), "Embedding/scoped retrieval candidate."
            if verifier:
                old_view, new_view = views[old.id], views[new.id]
                with tracer.start_as_current_span("enrichment.semantic_comparison"):
                    verification = await _verification(
                        database, verifier, old_view, new_view, verification_cache
                    )
                calls += verification.model_calls
                input_tokens += verification.usage.input_tokens
                output_tokens += verification.usage.output_tokens
                latency += verification.usage.latency_ms
                validation_failures += verification.usage.validation_failures
                if verification.model_calls:
                    costs.append(verification.usage.cost)
                confidence = verification.judgment.confidence
                explanation = verification.judgment.rationale
                outcome = verification.judgment.outcome
                if outcome == "unknown":
                    continue
                if outcome in {
                    "distinct",
                    "compatible_extension",
                    "more_specific_guidance",
                }:
                    action = "keep_distinct"
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
                cost=sum(value for value in costs if value is not None)
                if costs and all(value is not None for value in costs)
                else None,
                validation_failures=validation_failures,
            ),
        )

    return Check("semantic_duplicate", evaluate, provider_version)
