from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from ekb.domain.assessment import Applicability
from ekb.domain.models import Model, Scope
from ekb.domain.resolution import overlaps


class WorkloadContext(Model):
    exposure: Literal["public", "private"] | None = None
    criticality: Literal["normal", "high"] | None = None


class VerificationOption(Model):
    id: str = Field(min_length=1, max_length=100)
    check_id: str = Field(min_length=1, max_length=100)
    resource_type: str = Field(min_length=1, max_length=200)


class BaselineRequirement(Model):
    concept_id: UUID
    version_id: UUID
    scope: Scope = Field(default_factory=Scope)
    when: WorkloadContext = Field(default_factory=WorkloadContext)
    excluded_reason: str | None = Field(default=None, min_length=1)
    options: tuple[VerificationOption, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def unique_options(self) -> Self:
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("Baseline options must have distinct IDs")
        return self


class BaselinePolicy(Model):
    id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=1000)
    scope: Scope = Field(default_factory=Scope)
    requirements: tuple[BaselineRequirement, ...] = Field(min_length=1, max_length=200)
    completeness: Literal["explicit_selected_baseline"] = "explicit_selected_baseline"

    @model_validator(mode="after")
    def unique_requirements(self) -> Self:
        if len({item.concept_id for item in self.requirements}) != len(self.requirements):
            raise ValueError("Baseline cannot repeat requirement identities")
        return self


class BaselineApproval(Model):
    concept_id: UUID
    version_id: UUID


class DesignDecision(Model):
    concept_id: UUID
    version_id: UUID
    option: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=2000)


class DesignDocument(Model):
    context: WorkloadContext
    repository: str | None = Field(default=None, max_length=2000)
    decisions: tuple[DesignDecision, ...] = Field(default=(), max_length=200)

    @model_validator(mode="after")
    def unique_decisions(self) -> Self:
        if len({item.concept_id for item in self.decisions}) != len(self.decisions):
            raise ValueError(
                "Conflicting decisions for one requirement must be resolved explicitly"
            )
        return self


def requirement_applicability(
    requirement: BaselineRequirement, scope: Scope, context: WorkloadContext
) -> tuple[Applicability, str]:
    if not overlaps(requirement.scope, scope):
        return "not_applicable", "The approved requirement scope excludes this target."
    # Unknown target facets are not evidence that a scoped requirement is inapplicable.
    for field in (
        "organization",
        "business_unit",
        "jurisdiction",
        "platform",
        "application",
        "environment",
    ):
        if getattr(requirement.scope, field) and not getattr(scope, field):
            return "unknown", f"Target scope is missing {field}."
    for field in ("exposure", "criticality"):
        required, actual = getattr(requirement.when, field), getattr(context, field)
        if required and actual and required != actual:
            return "not_applicable", f"The approved {field} condition does not match."
    for field in ("exposure", "criticality"):
        if getattr(requirement.when, field) and not getattr(context, field):
            return "unknown", f"Workload context is missing {field}."
    if requirement.excluded_reason:
        return "not_applicable", requirement.excluded_reason
    return "applicable", "Included by the explicitly selected enterprise baseline."
