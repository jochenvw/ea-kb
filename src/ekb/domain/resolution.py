from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from ekb.domain.benchmarks import BenchmarkPassage
from ekb.domain.models import (
    ConceptDetail,
    ConceptVersion,
    Model,
    Provenance,
    RelationshipKind,
    Scope,
    now,
)

type ResolutionProfile = Literal["canonical", "best_available", "historical", "all"]


class Finding(Model):
    id: UUID
    check_id: str
    check_version: str
    input_revision: str
    action: Literal[
        "alias",
        "recognize_identity",
        "keep_distinct",
        "relationship",
        "version",
        "canonical_review",
    ]
    concept_ids: tuple[UUID, ...]
    version_ids: tuple[UUID, ...] = ()
    occurrence_ids: tuple[UUID, ...] = ()
    passages: tuple[BenchmarkPassage, ...] = ()
    relationship: RelationshipKind | None = None
    scope: Scope = Field(default_factory=Scope)
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1)
    uncertainty: str | None = None
    proposed_change: str | None = None
    external_reference: str | None = None
    deterministic: bool = False
    allow_prerelease: bool = False
    recorded_at: datetime = Field(default_factory=now)
    run_id: UUID | None = None
    proposed_by: str = "rule"


class Decision(Model):
    id: UUID = Field(default_factory=uuid4)
    finding_id: UUID
    outcome: Literal["accepted", "rejected", "deferred", "reversed"]
    actor: str = Field(min_length=1, max_length=200)
    actor_kind: Literal["human", "rule", "model"] = "human"
    rationale: str = Field(min_length=1, max_length=4000)
    recorded_at: datetime = Field(default_factory=now)
    effective_at: date = Field(default_factory=lambda: now().date())
    effective_until: date | None = None
    previous_id: UUID | None = None
    policy: str | None = None

    @model_validator(mode="after")
    def valid_period(self) -> Decision:
        if self.effective_until and self.effective_until < self.effective_at:
            raise ValueError("Decision ends before it starts")
        if self.outcome == "reversed" and not self.previous_id:
            raise ValueError("Reversal requires the previous decision ID")
        return self


class Resolution(Model):
    requested_id: UUID | None = None
    resolved_id: UUID | None = None
    profile: ResolutionProfile = "canonical"
    status: Literal["resolved", "ambiguous", "not_established"]
    scope: Scope
    as_of: date
    authoritative: bool = False
    versions: tuple[ConceptVersion, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    decisions: tuple[Decision, ...] = ()
    findings: tuple[Finding, ...] = ()
    identity_chain: tuple[UUID, ...] = ()
    explanation: str


class KnowledgeSnapshot(Model):
    revision: str
    governance_revision: str
    concepts: tuple[ConceptDetail, ...]
    current_version_ids: frozenset[UUID]
    findings: tuple[Finding, ...] = ()
    decisions: tuple[Decision, ...] = ()
    active_finding_ids: frozenset[UUID] = frozenset()
    preview_version_ids: frozenset[UUID] = frozenset()


class Deviation(Model):
    id: UUID
    concept_id: UUID
    solution: str
    scope: Scope
    approved: bool
    effective_from: date
    effective_until: date | None = None
    evidence_occurrence_id: UUID


def version_evidence(concept: ConceptDetail, version_id: UUID) -> tuple[UUID, ...]:
    versions = {v.id: v for v in concept.versions}
    visited: set[UUID] = set()
    while version_id in versions and version_id not in visited:
        visited.add(version_id)
        refs = tuple(o.id for o in concept.occurrences if o.concept_version_id == version_id)
        if refs:
            return refs
        version = versions[version_id]
        previous = versions.get(version.predecessor_id) if version.predecessor_id else None
        if previous is None or previous.candidate != version.candidate:
            break
        version_id = previous.id
    raise ValueError(
        "Canonical version needs source evidence or an unchanged evidenced predecessor"
    )


def overlaps(left: Scope, right: Scope) -> bool:
    for field in (
        "organization",
        "business_unit",
        "jurisdiction",
        "platform",
        "application",
        "environment",
    ):
        a, b = getattr(left, field), getattr(right, field)
        if a and b and a.casefold() != b.casefold():
            return False
    return not (
        left.effective_to
        and right.effective_from
        and left.effective_to < right.effective_from
        or right.effective_to
        and left.effective_from
        and right.effective_to < left.effective_from
    )


def applicable(scope: Scope, query: Scope, as_of: date) -> bool:
    for field in (
        "organization",
        "business_unit",
        "jurisdiction",
        "platform",
        "application",
        "environment",
    ):
        required, actual = getattr(scope, field), getattr(query, field)
        if required and (not actual or required.casefold() != actual.casefold()):
            return False
    return not (
        scope.effective_from
        and scope.effective_from > as_of
        or scope.effective_to
        and scope.effective_to < as_of
    )


def decision_heads(decisions: tuple[Decision, ...]) -> dict[UUID, Decision]:
    heads: dict[UUID, Decision] = {}
    for decision in sorted(decisions, key=lambda d: (d.recorded_at, str(d.id))):
        heads[decision.finding_id] = decision
    return heads


def resolve_alias(identity: UUID, aliases: dict[UUID, UUID]) -> tuple[UUID, ...]:
    chain = [identity]
    while chain[-1] in aliases:
        target = aliases[chain[-1]]
        if target in chain or len(chain) >= 100:
            raise ValueError("Cyclic or excessive alias chain")
        chain.append(target)
    return tuple(chain)


def alias_edges(findings: Iterable[Finding]) -> dict[UUID, UUID]:
    aliases: dict[UUID, UUID] = {}
    for finding in findings:
        if finding.action != "alias":
            continue
        if len(finding.concept_ids) != 2 or finding.concept_ids[0] == finding.concept_ids[1]:
            raise ValueError("Aliases require two different identities")
        alias_id, target_id = finding.concept_ids
        if alias_id in aliases and aliases[alias_id] != target_id:
            raise ValueError("Competing accepted alias targets")
        aliases[alias_id] = target_id
    for identity in aliases:
        resolve_alias(identity, aliases)
    return aliases


def active_aliases(snapshot: KnowledgeSnapshot, as_of: date) -> dict[UUID, UUID]:
    heads = decision_heads(tuple(d for d in snapshot.decisions if d.effective_at <= as_of))
    findings = {finding.id: finding for finding in snapshot.findings}
    active: list[Finding] = []
    for decision in heads.values():
        if decision.outcome != "accepted" or (
            decision.effective_until is not None and decision.effective_until < as_of
        ):
            continue
        finding = findings.get(decision.finding_id)
        if finding is None:
            raise ValueError("Accepted decision refers to a missing finding")
        active.append(finding)
    return alias_edges(active)
