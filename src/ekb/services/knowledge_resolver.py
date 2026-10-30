from __future__ import annotations

from datetime import date
from uuid import UUID

from ekb.domain.models import REQUIREMENT_TYPES, AuthorityEvent, ConceptVersion, Scope, now
from ekb.domain.publication import project_authority
from ekb.domain.resolution import (
    Resolution,
    ResolutionProfile,
    active_aliases,
    applicable,
    decision_heads,
    resolve_alias,
)
from ekb.observability import tracer
from ekb.observability.metrics import count
from ekb.ports import UnitOfWorkFactory


class KnowledgeResolver:
    def __init__(self, database: UnitOfWorkFactory):
        self.database = database

    async def resolve_concept(
        self,
        concept_id: UUID,
        scope: Scope | None = None,
        profile: ResolutionProfile = "canonical",
        as_of: date | None = None,
    ) -> Resolution:
        query_scope, when = scope or Scope(), as_of or now().date()
        if profile == "historical" and as_of is None:
            raise ValueError("Historical resolution requires an explicit applicability date")
        with tracer.start_as_current_span(
            "knowledge.resolve",
            attributes={
                "concept.id": str(concept_id),
                "resolution.profile": profile,
            },
        ) as span:
            async with self.database() as uow:
                snapshot = await uow.resolution.snapshot()
                try:
                    aliases = active_aliases(snapshot, when)
                    chain = resolve_alias(concept_id, aliases)
                except ValueError as error:
                    return Resolution(
                        requested_id=concept_id,
                        profile=profile,
                        status="ambiguous",
                        scope=query_scope,
                        as_of=when,
                        explanation=str(error),
                    )
                target = chain[-1]
                members = {
                    c.id for c in snapshot.concepts if resolve_alias(c.id, aliases)[-1] == target
                }
                details = [c for c in snapshot.concepts if c.id in members]
                provenance_items = []
                for cid in sorted(members, key=str):
                    provenance_items.extend(await uow.knowledge.provenance(cid))
                provenance = tuple(provenance_items)
            versions = [v for c in details for v in c.versions]
            heads = decision_heads(tuple(d for d in snapshot.decisions if d.effective_at <= when))
            findings = {f.id: f for f in snapshot.findings}
            decisions = [
                d
                for d in heads.values()
                if d.outcome == "accepted"
                and d.effective_at <= when
                and (d.effective_until is None or d.effective_until >= when)
                and d.finding_id in findings
            ]
            own_decisions = tuple(
                d for d in decisions if members.intersection(findings[d.finding_id].concept_ids)
            )
            if profile == "all":
                own_decisions = tuple(
                    d
                    for d in snapshot.decisions
                    if d.finding_id in findings
                    and members.intersection(findings[d.finding_id].concept_ids)
                )
            superseded = {
                f.concept_ids[1]
                for d in decisions
                if (f := findings[d.finding_id]).action == "version"
                and len(f.concept_ids) == 2
                and applicable(f.scope, query_scope, when)
            }
            eligible = [
                v
                for v in versions
                if applicable(v.candidate.scope, query_scope, when)
                and not (
                    v.effective_from
                    and v.effective_from > when
                    or v.effective_to
                    and v.effective_to < when
                )
            ]
            canonical = []
            for detail in details:
                states = project_authority(
                    [AuthorityEvent.model_validate(e) for e in detail.authority_events],
                    when,
                    [v.id for v in detail.versions],
                )
                for version in eligible:
                    if version.concept_id != detail.id or version.concept_id in superseded:
                        continue
                    accepted = [
                        findings[d.finding_id]
                        for d in decisions
                        if findings[d.finding_id].action == "canonical_review"
                        and version.id in findings[d.finding_id].version_ids
                        and applicable(findings[d.finding_id].scope, query_scope, when)
                    ]
                    state = any(
                        s.concept_version_id == version.id
                        and s.governance_status == "approved"
                        and s.canonical_status == "canonical"
                        and applicable(s.scope, query_scope, when)
                        for s in states
                    )
                    retired = version.publication.publication_status in {
                        "withdrawn",
                        "superseded",
                        "deprecated",
                    }
                    immature = (
                        version.id in snapshot.preview_version_ids
                        or bool(version.semantic_version and version.semantic_version.prerelease)
                        or (
                            version.publication.publication_status
                            in {
                                "draft",
                                "working_draft",
                                "proposed",
                                "consultation",
                                "preview",
                                "alpha",
                                "beta",
                                "release_candidate",
                            }
                        )
                    )
                    evidenced = bool(provenance)
                    if (
                        accepted
                        and state
                        and not retired
                        and evidenced
                        and (not immature or any(f.allow_prerelease for f in accepted))
                    ):
                        canonical.append(version)
            canonical = list({v.id: v for v in canonical}.values())
            versions_by_id = {v.id: v for v in versions}
            superseded_versions = set()
            for candidate in canonical:
                visited = {candidate.id}
                predecessor = candidate.predecessor_id
                while predecessor:
                    if predecessor in visited:
                        raise ValueError("Cyclic version predecessors")
                    visited.add(predecessor)
                    superseded_versions.add(predecessor)
                    previous = versions_by_id.get(predecessor)
                    predecessor = previous.predecessor_id if previous else None
            canonical = [v for v in canonical if v.id not in superseded_versions]
            chosen: list[ConceptVersion] = canonical
            authoritative = len(canonical) == 1
            if profile == "all":
                chosen, authoritative = versions, False
            elif profile == "best_available" and not canonical:
                chosen = [
                    v
                    for v in eligible
                    if v.concept_id not in superseded
                    and v.id in snapshot.current_version_ids
                    and v.publication.publication_status
                    not in {"withdrawn", "superseded", "deprecated"}
                ]
            status = (
                "resolved" if len(chosen) == 1 else ("ambiguous" if chosen else "not_established")
            )
            span.set_attribute("resolution.ambiguity", status == "ambiguous")
            count("knowledge.resolutions", status=status, profile=profile)
            related = set()
            frontier = set(members)
            while frontier and len(related) <= 100:
                next_ids = {
                    cid
                    for d in decisions
                    if (f := findings[d.finding_id]).relationship
                    in {"refines", "mapsTo", "supports", "dependsOn"}
                    and f.concept_ids[0] in frontier
                    and applicable(f.scope, query_scope, when)
                    for cid in f.concept_ids[1:]
                }
                next_ids -= related | members
                related.update(next_ids)
                frontier = next_ids
            if len(related) > 100:
                raise ValueError("Provenance traversal exceeds 100 related concepts")
            async with self.database() as uow:
                related_provenance = []
                for cid in sorted(related, key=str):
                    related_provenance.extend(await uow.knowledge.provenance(cid))
            return Resolution(
                requested_id=concept_id,
                resolved_id=target if members else None,
                profile=profile,
                status=status,
                scope=query_scope,
                as_of=when,
                authoritative=authoritative and profile != "all",
                versions=tuple(chosen),
                provenance=provenance + tuple(related_provenance),
                decisions=own_decisions,
                findings=tuple(findings[d.finding_id] for d in own_decisions),
                identity_chain=chain,
                explanation=(
                    "Unique approved, effective and evidenced canonical version selected through "
                    "accepted scoped reconciliation."
                    if authoritative
                    else "Complete history; no authority implied."
                    if profile == "all"
                    else "Multiple applicable versions remain; no arbitrary canonical selection."
                    if status == "ambiguous"
                    else "Best available candidate only; canonical authority is not established."
                    if profile == "best_available" and chosen
                    else "Canonical authority is not established; no candidate fallback."
                ),
            )

    async def resolve_requirement(
        self,
        query: str,
        scope: Scope | None = None,
        profile: ResolutionProfile = "canonical",
        as_of: date | None = None,
        limit: int = 50,
    ) -> tuple[Resolution, ...]:
        async with self.database() as uow:
            concepts = await uow.knowledge.search(query, min(max(limit, 1), 200))
        results, seen = [], set()
        for concept in concepts:
            if not any(v.candidate.semantic_type in REQUIREMENT_TYPES for v in concept.versions):
                continue
            result = await self.resolve_concept(concept.id, scope, profile, as_of)
            key = result.resolved_id or concept.id
            if key not in seen:
                results.append(result)
                seen.add(key)
        return tuple(results)

    async def get_current_canonical(
        self, concept_id: UUID, scope: Scope | None = None
    ) -> Resolution:
        return await self.resolve_concept(concept_id, scope)

    async def trace_provenance(self, concept_id: UUID):
        return (await self.resolve_concept(concept_id, profile="all")).provenance

    async def get_version_history(self, concept_id: UUID):
        return (await self.resolve_concept(concept_id, profile="all")).versions

    async def explain_resolution(self, concept_id: UUID, scope: Scope | None = None) -> Resolution:
        return await self.resolve_concept(concept_id, scope)
