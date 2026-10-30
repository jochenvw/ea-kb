from __future__ import annotations

import json
import math
import re
from difflib import SequenceMatcher
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, ValidationInfo, field_validator

from ekb.domain.models import Model, Scope, SemanticType, Usage

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
type VerificationStatus = Literal["completed", "failed", "context_limit"]

_INCOMPLETE_METHODS = frozenset({"model_failed", "context_limit"})


class SemanticAssertion(Model):
    occurrence_id: UUID
    statement: str = Field(min_length=1, max_length=4000)
    excerpt: str = Field(min_length=1)
    semantic_type: SemanticType | None = None
    scope: Scope = Field(default_factory=Scope)


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
    status: VerificationStatus = Field(default="completed", validate_default=True)
    model_id: str | None = None
    usage: Usage = Field(default_factory=Usage)
    model_calls: int = Field(default=0, ge=0)

    @field_validator("status", mode="before")
    @classmethod
    def legacy_failure_status(cls, status: object, info: ValidationInfo) -> object:
        method: object = info.data.get("method")
        if method == "model_failed":
            return "failed"
        if method == "context_limit":
            return "context_limit"
        return status

    @property
    def completed(self) -> bool:
        return self.status == "completed"


class EmbeddingResult(Model):
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int = Field(default=0, ge=0)
    calls: int = Field(default=0, ge=0)
    usage_complete: bool | None = Field(default=None, strict=True)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class CachedEmbedding(Model):
    kind: Literal["embedding"] = "embedding"
    vector: tuple[Annotated[float, Field(strict=True)], ...] = Field(min_length=1, max_length=16384)

    @field_validator("vector")
    @classmethod
    def valid_vector(cls, vector: tuple[float, ...]) -> tuple[float, ...]:
        if not any(vector) or not all(math.isfinite(value) for value in vector):
            raise ValueError("Embedding vectors must be finite and nonzero")
        return vector


class CachedVerification(Model):
    kind: Literal["verification"] = "verification"
    judgment: SemanticJudgment
    method: str
    status: Literal["completed"] = "completed"
    model_id: str | None = None

    @field_validator("method")
    @classmethod
    def completed_method(cls, method: str) -> str:
        if method in _INCOMPLETE_METHODS:
            raise ValueError("Incomplete verification cannot be cached")
        return method


type SemanticCacheEntry = Annotated[
    CachedEmbedding | CachedVerification, Field(discriminator="kind")
]


def normalized(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


def semantic_input(assertion: SemanticAssertion) -> str:
    return json.dumps(
        {
            "statement": assertion.statement,
            "excerpt": assertion.excerpt,
            "semantic_type": assertion.semantic_type,
            "scope": assertion.scope.model_dump(mode="json"),
        },
        sort_keys=True,
    )


def structural_verification(old: SemanticAssertion, new: SemanticAssertion) -> Verification:
    outcome: SemanticOutcome = "unknown"
    change: ChangeClass | None = None
    confidence = 0.35
    rationale = "Structural candidate only; semantic equivalence requires review."
    old_text, new_text = old.excerpt, new.excerpt
    scopes_differ = any(
        old_value is not None and new_value is not None and old_value != new_value
        for old_value, new_value in zip(
            old.scope.model_dump().values(), new.scope.model_dump().values(), strict=True
        )
    )
    types_differ = (
        old.semantic_type is not None
        and new.semantic_type is not None
        and old.semantic_type != new.semantic_type
    )
    if old.occurrence_id == new.occurrence_id and old_text == new_text:
        outcome, confidence = "exact_duplicate", 1
        rationale = "The same exact persisted source occurrence."
    elif scopes_differ or types_differ:
        outcome, confidence = "distinct", 1
        rationale = "Different explicit scope or ontology classification; keep assertions separate."
    elif old.scope != new.scope or old.semantic_type is None or new.semantic_type is None:
        rationale = "Missing scope or ontology classification requires review."
    elif normalized(old_text) == normalized(new_text):
        outcome, confidence = "equivalent", 1
        rationale = "Exactly normalized source statements within the same explicit scope."
        if old_text != new_text:
            change = "PATCH"
    else:
        similarity = SequenceMatcher(
            None, normalized(old_text), normalized(new_text), autojunk=True
        ).ratio()
        if similarity >= 0.65:
            negation = r"\b(not|never|prohibit|deny|unless)\b"
            if bool(re.search(negation, old_text, re.I)) != bool(
                re.search(negation, new_text, re.I)
            ):
                outcome, change, confidence = "potential_conflict", "MAJOR", 0.65
                rationale = "Related statements change explicit negation; review the difference."
            else:
                outcome, confidence = "more_specific_guidance", 0.65
                rationale = "Closely matching text suggests related guidance, not equivalence."
        elif similarity < 0.35:
            outcome, confidence = "distinct", 0.65
            rationale = "Low textual similarity; shared terminology does not establish identity."
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
