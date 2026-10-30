from __future__ import annotations

import json
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Literal
from uuid import UUID

from pydantic import Field

from ekb.domain.benchmarks import (
    BenchmarkControl,
    BenchmarkSuccessor,
    FrameworkReference,
    NativeControlBinding,
    PolicyDefinition,
    PolicyReference,
)
from ekb.domain.models import ConceptDetail, Model, PublicationMetadata, Scope, Usage, now

type SemanticOutcome = Literal[
    "exact_duplicate",
    "equivalent",
    "compatible_extension",
    "more_specific_guidance",
    "distinct",
    "potential_conflict",
    "unknown",
]
type ChangeClass = Literal["PATCH", "MINOR", "MAJOR"]
type LineageOutcome = Literal[
    "same_semantic_concept",
    "updated_version",
    "superseded_requirement",
    "similar_but_distinct",
    "new_requirement",
    "removed_requirement",
    "split_candidate",
    "merge_candidate",
    "unknown",
]


class MappingBinding(Model):
    kind: Literal["mapsTo"] = "mapsTo"
    status: Literal["published"] = "published"
    reference: FrameworkReference
    native_controls: tuple[NativeControlBinding, ...] = ()
    note: str = (
        "Published identifier mapping, not semantic equivalence, per-clause coverage or compliance."
    )


class PolicyBinding(Model):
    reference: PolicyReference
    definition: PolicyDefinition | None = None
    version_matches_reference: bool | None = None


class ControlView(Model):
    snapshot_id: UUID
    revision_id: UUID
    segment_id: UUID
    retrieved_at: datetime
    control: BenchmarkControl
    scope: Scope = Field(default_factory=lambda: Scope(platform="Azure"))
    concept_ids: dict[str, UUID] = Field(default_factory=dict)
    mappings: tuple[MappingBinding, ...] = ()
    policies: tuple[PolicyBinding, ...] = ()


class EditionView(Model):
    edition: str
    publication: PublicationMetadata
    source_url: str | None = None
    snapshot_id: UUID | None = None
    successors: tuple[BenchmarkSuccessor, ...] = ()


class SemanticJudgment(Model):
    outcome: SemanticOutcome
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=4000)
    old_excerpt: str = Field(min_length=1)
    new_excerpt: str = Field(min_length=1)
    change_class: ChangeClass | None = None


class Verification(Model):
    judgment: SemanticJudgment
    method: str
    model_id: str | None = None
    usage: Usage = Field(default_factory=Usage)
    model_calls: int = 0
    cache_hit: bool = False


class ControlComparison(Model):
    old: ControlView | None = None
    new: ControlView | None = None
    exact_identity: bool = False
    structural_similarity: float = 0
    embedding_similarity: float | None = None
    verification: Verification | None = None
    lineage: LineageOutcome
    comparison_kind: Literal["overlap", "succession", "conflict"] | None = None
    review_status: Literal["proposed"] = "proposed"
    confidence: float = Field(default=0.5, ge=0, le=1)
    changed_fields: tuple[str, ...] = ()
    proposed_change: ChangeClass | None = None
    proposed_semantic_version: str | None = None
    version_note: str = "No established numeric baseline; no version number is invented."
    alternatives: tuple[str, ...] = ()
    guidance_diff: str = ""


class ComparisonReport(Model):
    id: UUID
    fingerprint: str
    from_edition: str
    to_edition: str
    control_id: str | None = None
    recorded_at: datetime = Field(default_factory=now)
    editions: tuple[EditionView, ...]
    source_snapshot_ids: tuple[UUID, ...]
    changes: tuple[ControlComparison, ...]
    added_control_ids: tuple[str, ...] = ()
    removed_control_ids: tuple[str, ...] = ()
    matcher_version: str
    cache_hit: bool = False
    model_calls: int = 0
    embedding_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failures: int = 0
    notes: tuple[str, ...] = (
        "All semantic transitions require review; none change canonical authority.",
    )


class ControlContext(Model):
    control: ControlView
    edition: EditionView
    concept: ConceptDetail | None = None


class LineageProposalReference(Model):
    comparison_id: UUID
    proposal: ControlComparison


class BenchmarkLineage(Model):
    context: ControlContext
    published_edition_lineage: tuple[BenchmarkSuccessor, ...]
    semantic_proposals: tuple[LineageProposalReference, ...]
    note: str = "Published MAPS_TO is not derivation, equivalence, coverage or adoption."


class EmbeddingRecord(Model):
    id: UUID
    model_id: str
    text_hash: str
    vector: tuple[float, ...] = Field(min_length=1, max_length=16384)


class EmbeddingResult(Model):
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int = 0
    calls: int = 0


def normalized(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


def core(control: BenchmarkControl) -> str:
    return control.security_principle or control.title


def semantic_input(view: ControlView) -> str:
    control = view.control
    return json.dumps(
        {
            "control_id": control.control_id,
            "title": control.title,
            "domain": control.domain,
            "scope": view.scope.model_dump(mode="json"),
            "principle": core(control),
            "guidance": control.azure_guidance,
            "generic_guidance": control.generic_guidance,
            "risk": control.risk_to_mitigate,
            "subcontrols": [(c.control_id, c.title, c.text) for c in control.subcontrols],
            "alternatives": control.implementation_options,
            "are_alternatives": control.implementation_options_are_alternatives,
        },
        sort_keys=True,
    )


def structural_similarity(old: BenchmarkControl, new: BenchmarkControl) -> float:
    title = SequenceMatcher(None, normalized(old.title), normalized(new.title)).ratio()
    statement = SequenceMatcher(None, normalized(core(old)), normalized(core(new))).ratio()
    return round(0.55 * title + 0.45 * statement, 6)


def changed_fields(old: BenchmarkControl, new: BenchmarkControl) -> tuple[str, ...]:
    fields = (
        "control_id",
        "title",
        "security_principle",
        "risk_to_mitigate",
        "generic_guidance",
        "azure_guidance",
        "criticality",
        "implementation_options",
        "implementation_options_are_alternatives",
    )
    changed = [name for name in fields if getattr(old, name) != getattr(new, name)]
    if [(c.control_id, c.title, c.text) for c in old.subcontrols] != [
        (c.control_id, c.title, c.text) for c in new.subcontrols
    ]:
        changed.append("subcontrols")
    if {(r.framework, r.revision, r.control_id) for r in old.mappings} != {
        (r.framework, r.revision, r.control_id) for r in new.mappings
    }:
        changed.append("mappings")
    return tuple(changed)


def structural_verification(old: ControlView, new: ControlView) -> Verification:
    a, b = old.control, new.control
    old_text, new_text = core(a), core(b)
    changed = changed_fields(a, b)
    outcome: SemanticOutcome = "unknown"
    change: ChangeClass | None = None
    confidence = 0.35
    rationale = "Structural candidate only; semantic equivalence has not been verified by a model."
    if old.scope != new.scope or a.domain != b.domain:
        outcome, change, confidence = "distinct", None, 1
        rationale = "Different explicit scope or control domain; no equivalence is proposed."
    elif (
        old.segment_id == new.segment_id
        and a.evidence.start == b.evidence.start
        and a.evidence.excerpt == b.evidence.excerpt
    ):
        outcome, confidence = "exact_duplicate", 1
        rationale = "The same exact persisted source occurrence."
    elif normalized(old_text) == normalized(new_text):
        outcome, confidence = "equivalent", 1
        rationale = "Exactly normalized core statements within the same explicit scope."
        if old_text != new_text:
            change = "PATCH"
        if {"azure_guidance", "generic_guidance", "subcontrols"}.intersection(changed):
            outcome, confidence = "more_specific_guidance", 0.65
            change = None
            rationale += " Guidance differs; backward compatibility still requires review."
    elif structural_similarity(a, b) >= 0.65:
        negation = r"\b(not|never|prohibit|deny|unless)\b"
        if bool(re.search(negation, old_text, re.I)) != bool(re.search(negation, new_text, re.I)):
            outcome, change, confidence = "potential_conflict", "MAJOR", 0.65
            rationale = "Related statements change explicit negation; possible breaking change."
        else:
            outcome, confidence = "more_specific_guidance", 0.65
            if (
                normalized(a.title) == normalized(b.title)
                and len(b.subcontrols) > len(a.subcontrols)
                and re.findall(r"\d+(?:\.\d+)*", old_text) == re.findall(r"\d+(?:\.\d+)*", new_text)
            ):
                change = "MINOR"
            rationale = (
                "Closely matching names and principles suggest related guidance, not proven "
                "equivalence. An additive subcontrol suggests MINOR, if present; changed "
                "obligations and backward compatibility still require semantic review."
            )
    elif structural_similarity(a, b) < 0.35:
        outcome, confidence = "distinct", 0.65
        rationale = "Low structural similarity; shared identifiers or vocabulary are insufficient."
    return Verification(
        judgment=SemanticJudgment(
            outcome=outcome,
            confidence=confidence,
            rationale=rationale,
            old_excerpt=old_text,
            new_excerpt=new_text,
            change_class=change,
        ),
        method="structural_rules",
    )
