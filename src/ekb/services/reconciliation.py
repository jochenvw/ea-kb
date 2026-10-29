from __future__ import annotations

import math
from collections import Counter
from difflib import unified_diff

from ekb.domain.benchmarks import BenchmarkControl, StoredBenchmarkDocument
from ekb.domain.models import PublicationMetadata, Usage, fingerprint, stable_id
from ekb.domain.reconciliation import (
    BenchmarkLineage,
    ComparisonReport,
    ControlComparison,
    ControlContext,
    ControlView,
    EditionView,
    EmbeddingRecord,
    LineageProposalReference,
    MappingBinding,
    PolicyBinding,
    Verification,
    changed_fields,
    core,
    semantic_input,
    structural_similarity,
    structural_verification,
)
from ekb.observability import tracer
from ekb.ports import EmbeddingPort, SemanticVerifier, UnitOfWorkFactory

MATCHER_VERSION = "benchmark-reconciliation-v1"


class BenchmarkService:
    def __init__(
        self,
        database: UnitOfWorkFactory,
        verifier: SemanticVerifier | None = None,
        embeddings: EmbeddingPort | None = None,
    ):
        self.database, self.verifier, self.embeddings = database, verifier, embeddings

    async def _inputs(
        self, editions: tuple[str, ...], control_id: str | None = None
    ) -> tuple[
        list[StoredBenchmarkDocument], tuple[EditionView, ...], dict[str, dict[str, ControlView]]
    ]:
        async with self.database() as uow:
            documents = [
                document
                for edition in dict.fromkeys(editions)
                for document in await uow.benchmarks.documents(edition, limit=500)
            ]
            if not documents:
                raise ValueError("No cached benchmark records; sync sources and run ekb scan first")
            edition_views = []
            views: dict[str, dict[str, ControlView]] = {}
            mapping_cache: dict[tuple[str, str | None, str], MappingBinding] = {}
            definitions = {
                policy.definition_id.casefold(): policy
                for document in documents
                for policy in document.document.policies
            }
            for edition in dict.fromkeys(editions):
                edition_documents = [d for d in documents if d.edition == edition]
                root = next((d for d in edition_documents if d.role == "overview"), None)
                source = await uow.sources.get(root.source_id) if root else None
                edition_views.append(
                    EditionView(
                        edition=edition,
                        publication=root.document.publication if root else PublicationMetadata(),
                        source_url=source.item.location if source else None,
                        snapshot_id=root.id if root else None,
                        successors=root.document.successors if root else (),
                    )
                )
                selected: dict[str, tuple[StoredBenchmarkDocument, BenchmarkControl]] = {}
                for document in sorted(
                    edition_documents, key=lambda d: (d.role != "domain", str(d.id))
                ):
                    for control in document.document.controls:
                        if control_id is None or control.control_id == control_id:
                            selected.setdefault(control.control_id, (document, control))
                if not selected:
                    raise ValueError(
                        f"No controls for {edition}" + (f":{control_id}" if control_id else "")
                    )
                views[edition] = {}
                for key, (document, control) in selected.items():
                    references = [
                        *control.mappings,
                        *(
                            m.reference
                            for d in edition_documents
                            for m in d.document.mappings
                            if m.control_id == key and m.edition == edition
                        ),
                    ]
                    bindings = []
                    for reference in references:
                        cache_key = (reference.framework, reference.revision, reference.control_id)
                        if cache_key not in mapping_cache:
                            targets = (
                                await uow.benchmarks.native_controls(
                                    reference.framework, reference.revision, reference.control_id
                                )
                                if reference.revision
                                else []
                            )
                            mapping_cache[cache_key] = MappingBinding(
                                reference=reference, native_controls=tuple(targets)
                            )
                        targets = mapping_cache[cache_key].native_controls
                        resolution = (
                            "resolved"
                            if len(targets) == 1
                            else ("ambiguous" if targets else "unresolved")
                        )
                        bindings.append(
                            MappingBinding(
                                reference=reference.model_copy(
                                    update={
                                        "resolution": resolution,
                                        "resolved_concept_ids": targets[0].concept_ids
                                        if len(targets) == 1
                                        else (),
                                    }
                                ),
                                native_controls=targets,
                            )
                        )
                    policy_references = [
                        *control.policy_references,
                        *(
                            ref
                            for d in edition_documents
                            for ref in d.document.policy_references
                            if ref.evidence.section == key
                        ),
                    ]
                    policies = []
                    seen = set()
                    for reference in policy_references:
                        identity = (
                            reference.definition_id,
                            reference.evidence.source_id,
                            reference.evidence.start,
                        )
                        if identity in seen:
                            continue
                        seen.add(identity)
                        definition = definitions.get(reference.definition_id.casefold())
                        policies.append(
                            PolicyBinding(
                                reference=reference,
                                definition=definition,
                                version_matches_reference=(
                                    reference.reference_version == definition.version
                                    if definition
                                    and reference.reference_version
                                    and definition.version
                                    else None
                                ),
                            )
                        )
                    views[edition][key] = ControlView(
                        snapshot_id=document.id,
                        revision_id=document.revision_id,
                        segment_id=document.document.segment_id,
                        retrieved_at=document.retrieved_at,
                        control=control.model_copy(
                            update={"concept_id": document.concept_ids.get(key)}
                        ),
                        concept_ids=document.concept_ids,
                        mappings=tuple(bindings),
                        policies=tuple(policies),
                    )
        return documents, tuple(edition_views), views

    async def control(self, reference: str) -> ControlContext:
        edition, separator, control_id = reference.partition(":")
        if not separator or not edition or not control_id:
            raise ValueError("Use an edition-qualified control, e.g. mcsb-v2:NS-6")
        _, editions, views = await self._inputs((edition,), control_id)
        view = views[edition][control_id]
        async with self.database() as uow:
            concept = (
                await uow.knowledge.get(view.control.concept_id)
                if view.control.concept_id
                else None
            )
        return ControlContext(control=view, edition=editions[0], concept=concept)

    async def lineage(self, reference: str) -> BenchmarkLineage:
        context = await self.control(reference)
        edition, control_id = reference.split(":", 1)
        async with self.database() as uow:
            reports = await uow.benchmarks.comparisons(edition)
            documents = await uow.benchmarks.documents(role="overview")
        proposals = []
        seen = set()
        for report in reports:
            for change in report.changes:
                if not any(
                    v and v.control.edition == edition and v.control.control_id == control_id
                    for v in (change.old, change.new)
                ):
                    continue
                key = (
                    change.old.snapshot_id if change.old else None,
                    change.old.control.control_id if change.old else None,
                    change.new.snapshot_id if change.new else None,
                    change.new.control.control_id if change.new else None,
                )
                if key not in seen:
                    proposals.append(
                        LineageProposalReference(comparison_id=report.id, proposal=change)
                    )
                    seen.add(key)
        return BenchmarkLineage(
            context=context,
            published_edition_lineage=tuple(
                successor
                for d in documents
                for successor in d.document.successors
                if edition in (successor.predecessor, successor.successor)
            ),
            semantic_proposals=tuple(proposals),
        )

    async def _vectors(self, views: list[ControlView], read_only: bool):
        vectors: dict[tuple[str, str], tuple[float, ...]] = {}
        if self.embeddings is None or read_only:
            return vectors, 0, 0
        keys = {}
        missing = {}
        async with self.database() as uow:
            for view in views:
                identity = (view.control.edition, view.control.control_id)
                text = f"{view.scope.platform} / {view.control.domain}: {core(view.control)}"
                text_hash = fingerprint(text=text, provider=self.embeddings.fingerprint)
                key = stable_id("benchmark-embedding", text_hash)
                keys[identity] = key
                cached = await uow.benchmarks.embedding(key)
                if cached:
                    vectors[identity] = cached.vector
                else:
                    missing[key] = (text, text_hash)
        calls = tokens = 0
        if missing:
            result = await self.embeddings.embed([text for text, _ in missing.values()])
            if len(result.vectors) != len(missing):
                raise ValueError("Embedding provider returned the wrong number of vectors")
            cached_vectors = {}
            async with self.database() as uow:
                for (key, (_, text_hash)), vector in zip(
                    missing.items(), result.vectors, strict=True
                ):
                    if not vector or not all(math.isfinite(v) for v in vector) or not any(vector):
                        raise ValueError("Embedding provider returned an invalid vector")
                    await uow.benchmarks.save_embedding(
                        EmbeddingRecord(
                            id=key,
                            model_id=self.embeddings.model_id,
                            text_hash=text_hash,
                            vector=vector,
                        )
                    )
                    cached_vectors[key] = vector
                await uow.commit()
            for identity, key in keys.items():
                if key in cached_vectors:
                    vectors[identity] = cached_vectors[key]
            calls, tokens = result.calls, result.input_tokens
        return vectors, calls, tokens

    async def _verify(self, old: ControlView, new: ControlView, read_only: bool) -> Verification:
        baseline = structural_verification(old, new)
        if (
            self.verifier is None
            or read_only
            or baseline.judgment.outcome == "exact_duplicate"
            or semantic_input(old) == semantic_input(new)
            or old.scope != new.scope
            or old.control.domain != new.control.domain
        ):
            return baseline
        key = stable_id(
            "benchmark-verification",
            fingerprint(
                old=semantic_input(old), new=semantic_input(new), verifier=self.verifier.fingerprint
            ),
        )
        async with self.database() as uow:
            cached = await uow.benchmarks.verification(key)
        if cached:
            return cached.model_copy(update={"usage": Usage(), "model_calls": 0, "cache_hit": True})
        verification = await self.verifier.verify(old, new)
        if (
            verification.judgment.old_excerpt not in old.control.evidence.excerpt
            or verification.judgment.new_excerpt not in new.control.evidence.excerpt
        ):
            raise ValueError("Semantic verifier returned ungrounded evidence")
        if verification.method == "small_model":
            async with self.database() as uow:
                await uow.benchmarks.save_verification(key, verification)
                await uow.commit()
        return verification

    async def compare(
        self,
        from_edition: str,
        to_edition: str,
        control_id: str | None = None,
        *,
        read_only: bool = False,
    ) -> ComparisonReport:
        with tracer.start_as_current_span("benchmark.compare_editions"):
            documents, editions, views = await self._inputs((from_edition, to_edition), control_id)
            old, new = views[from_edition], views[to_edition]
            signature = fingerprint(
                implementation=MATCHER_VERSION,
                schema=ComparisonReport.model_json_schema(),
                snapshots=sorted(str(d.id) for d in documents),
                control=control_id,
                from_edition=from_edition,
                to_edition=to_edition,
                native=[
                    m.model_dump(mode="json")
                    for v in [*old.values(), *new.values()]
                    for m in v.mappings
                ],
                verifier=self.verifier.fingerprint if self.verifier else "structural_rules",
                embeddings=self.embeddings.fingerprint if self.embeddings else None,
            )
            comparison_id = stable_id("benchmark-comparison", signature)
            async with self.database() as uow:
                cached = await uow.benchmarks.comparison(comparison_id)
            if cached:
                return cached.model_copy(
                    update={
                        "cache_hit": True,
                        "model_calls": 0,
                        "embedding_calls": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                    }
                )
            vectors, embedding_calls, embedding_tokens = await self._vectors(
                [*old.values(), *new.values()], read_only
            )
            changes = []
            used = set()
            for target in new.values():
                with tracer.start_as_current_span("benchmark.match_concepts"):
                    candidates = []
                    for source in old.values():
                        if (
                            source.scope != target.scope
                            or source.control.domain != target.control.domain
                        ):
                            continue
                        structural = structural_similarity(source.control, target.control)
                        embedding = None
                        a = vectors.get((source.control.edition, source.control.control_id))
                        b = vectors.get((target.control.edition, target.control.control_id))
                        if a is not None and b is not None:
                            if len(a) != len(b):
                                raise ValueError(
                                    "Embedding dimensions changed; change provider fingerprint"
                                )
                            embedding = sum(x * y for x, y in zip(a, b, strict=True)) / (
                                math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
                            )
                        score = max(structural, embedding or 0)
                        if source.control.control_id == target.control.control_id:
                            score += 0.02
                        candidates.append((score, structural, embedding, source))
                    candidates.sort(key=lambda c: (-c[0], c[3].control.control_id))
                if not candidates or (
                    candidates[0][0] < 0.45
                    and candidates[0][3].control.control_id != target.control.control_id
                ):
                    changes.append(ControlComparison(new=target, lineage="new_requirement"))
                    continue
                score, structural, embedding, source = candidates[0]
                verification = await self._verify(source, target, read_only)
                used.add(source.control.control_id)
                fields = list(changed_fields(source.control, target.control))
                if [
                    (p.reference.definition_id, p.reference.reference_version)
                    for p in source.policies
                ] != [
                    (p.reference.definition_id, p.reference.reference_version)
                    for p in target.policies
                ]:
                    fields.append("policy_references")
                outcome = verification.judgment.outcome
                lineage = (
                    "same_semantic_concept"
                    if outcome in {"exact_duplicate", "equivalent"}
                    else "updated_version"
                    if outcome in {"compatible_extension", "more_specific_guidance"}
                    else "similar_but_distinct"
                    if outcome == "distinct"
                    else "superseded_requirement"
                    if outcome == "potential_conflict"
                    else "unknown"
                )
                alternatives = tuple(
                    candidate.control.control_id
                    for s, _, _, candidate in candidates[1:4]
                    if s >= 0.7 and s >= score - 0.05
                )
                changes.append(
                    ControlComparison(
                        old=source,
                        new=target,
                        exact_identity=(
                            source.control.benchmark == target.control.benchmark
                            and source.control.edition == target.control.edition
                            and source.control.control_id == target.control.control_id
                        ),
                        structural_similarity=structural,
                        embedding_similarity=embedding,
                        verification=verification,
                        confidence=verification.judgment.confidence,
                        relationship_kind=(
                            "CONFLICTS_WITH"
                            if outcome == "potential_conflict"
                            else "SUPERSEDES"
                            if lineage == "updated_version"
                            else "OVERLAPS_WITH"
                            if outcome in {"equivalent", "distinct"}
                            else None
                        ),
                        lineage="merge_candidate" if alternatives else lineage,
                        changed_fields=tuple(fields),
                        proposed_change=verification.judgment.change_class,
                        alternatives=alternatives,
                        guidance_diff="\n".join(
                            unified_diff(
                                source.control.azure_guidance.splitlines(),
                                target.control.azure_guidance.splitlines(),
                                fromfile=f"{from_edition}:{source.control.control_id}",
                                tofile=f"{to_edition}:{target.control.control_id}",
                                lineterm="",
                            )
                        ),
                    )
                )
            counts = Counter(c.old.control.control_id for c in changes if c.old)
            changes = [
                c.model_copy(update={"lineage": "split_candidate"})
                if c.old and counts[c.old.control.control_id] > 1
                else c
                for c in changes
            ]
            changes.extend(
                ControlComparison(old=source, lineage="removed_requirement")
                for key, source in old.items()
                if key not in used
            )
            with tracer.start_as_current_span("benchmark.propose_lineage") as span:
                report = ComparisonReport(
                    id=comparison_id,
                    fingerprint=signature,
                    from_edition=from_edition,
                    to_edition=to_edition,
                    control_id=control_id,
                    editions=editions,
                    source_snapshot_ids=tuple(sorted({d.id for d in documents}, key=str)),
                    changes=tuple(changes),
                    added_control_ids=tuple(sorted(new.keys() - old.keys())),
                    removed_control_ids=tuple(sorted(old.keys() - new.keys())),
                    matcher_version=MATCHER_VERSION,
                    model_calls=sum(c.verification.model_calls for c in changes if c.verification),
                    embedding_calls=embedding_calls,
                    input_tokens=embedding_tokens
                    + sum(c.verification.usage.input_tokens for c in changes if c.verification),
                    output_tokens=sum(
                        c.verification.usage.output_tokens for c in changes if c.verification
                    ),
                    failures=sum(
                        c.verification is not None
                        and c.verification.method in {"model_failed", "context_limit"}
                        for c in changes
                    ),
                    notes=(
                        "All transitions are proposals; canonical authority remains independent.",
                        "New/removed denotes absence from loaded records, "
                        "not proof about missing corpus content.",
                        "Read-only structural preview; not persisted."
                        if read_only
                        else "Persisted review-only comparison.",
                    ),
                )
                span.set_attributes(
                    {
                        "benchmark.exact_matches": sum(
                            c.verification is not None
                            and c.verification.judgment.outcome == "exact_duplicate"
                            for c in changes
                        ),
                        "benchmark.semantic_candidates": sum(
                            c.verification is not None
                            and c.verification.judgment.outcome
                            in {"equivalent", "compatible_extension", "more_specific_guidance"}
                            for c in changes
                        ),
                        "benchmark.new_controls": len(report.added_control_ids),
                        "benchmark.removed_controls": len(report.removed_control_ids),
                        "benchmark.version_proposals": sum(
                            c.proposed_change is not None for c in changes
                        ),
                        "model.calls": report.model_calls,
                        "model.input_tokens": report.input_tokens,
                        "model.output_tokens": report.output_tokens,
                        "embedding.calls": report.embedding_calls,
                        "benchmark.verification_failures": report.failures,
                    }
                )
            if report.failures:
                report = report.model_copy(
                    update={
                        "notes": (
                            *report.notes[:-1],
                            "Incomplete verification; not persisted or cached. "
                            "Retry after fixing provider configuration.",
                        )
                    }
                )
            elif not read_only:
                with tracer.start_as_current_span("benchmark.persist"):
                    async with self.database() as uow:
                        await uow.benchmarks.save_comparison(report)
                        await uow.commit()
            return report
