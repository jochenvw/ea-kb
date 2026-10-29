from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from ekb.domain.models import Model, Scope, fingerprint, now

type IssueStatus = Literal[
    "open", "under_review", "resolved", "dismissed", "accepted_risk", "reopened"
]


class Issue(Model):
    id: UUID
    issue_type: str
    severity: Literal["critical", "high", "medium", "low"]
    confidence: float = Field(ge=0, le=1)
    concept_ids: tuple[UUID, ...]
    scope: Scope = Field(default_factory=Scope)
    occurrence_ids: tuple[UUID, ...] = ()
    finding_ids: tuple[UUID, ...] = ()
    decision_ids: tuple[UUID, ...] = ()
    check_id: str
    check_version: str
    evidence_revision: str
    explanation: str
    options: tuple[str, ...] = ()
    first_detected: datetime = Field(default_factory=now)
    last_observed: datetime = Field(default_factory=now)
    status: IssueStatus = "open"
    observed: bool = True
    owner: str | None = None


class IssueEvent(Model):
    id: UUID = Field(default_factory=uuid4)
    issue_id: UUID
    action: str
    actor: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    recorded_at: datetime = Field(default_factory=now)
    run_id: UUID | None = None
    evidence_revision: str


class IssueDetail(Model):
    issue: Issue
    events: tuple[IssueEvent, ...]


def consolidate(issues: tuple[Issue, ...]) -> tuple[Issue, ...]:
    groups: dict[UUID, list[Issue]] = {}
    for issue in issues:
        groups.setdefault(issue.id, []).append(issue)
    result: list[Issue] = []
    for values in groups.values():
        if len(values) == 1:
            result.append(values[0])
            continue
        ordered = sorted(values, key=lambda i: i.evidence_revision)
        result.append(
            ordered[0].model_copy(
                update={
                    "finding_ids": tuple(
                        sorted({f for i in values for f in i.finding_ids}, key=str)
                    ),
                    "occurrence_ids": tuple(
                        sorted({o for i in values for o in i.occurrence_ids}, key=str)
                    ),
                    "evidence_revision": fingerprint(
                        revisions=sorted(i.evidence_revision for i in values)
                    ),
                    "explanation": f"{len(values)} related conditions. " + ordered[0].explanation,
                }
            )
        )
    return tuple(result)
