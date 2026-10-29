from __future__ import annotations

from uuid import UUID

from sqlalchemy import Date, Float, case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ekb.adapters.persistence.postgres.models import DecisionRow, IssueEventRow, IssueRow
from ekb.adapters.persistence.postgres.workflow import governance_lock
from ekb.domain.issues import Issue, IssueDetail, IssueEvent, IssueStatus, consolidate
from ekb.domain.models import Scope, now, stable_id
from ekb.domain.resolution import Decision, decision_heads
from ekb.observability import tracer
from ekb.observability.metrics import count


class Issues:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(
        self,
        status: IssueStatus | None = None,
        issue_type: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        scope: Scope | None = None,
    ) -> list[Issue]:
        ranks = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        statement = select(IssueRow).order_by(
            case(ranks, value=IssueRow.payload["severity"].astext, else_=4),
            cast(IssueRow.payload["confidence"].astext, Float).desc(),
            IssueRow.id,
        )
        if status:
            statement = statement.where(IssueRow.status == status)
        if issue_type:
            statement = statement.where(IssueRow.issue_type == issue_type)
        if severity:
            statement = statement.where(IssueRow.payload["severity"].astext == severity)
        if scope:
            for field in (
                "organization",
                "business_unit",
                "jurisdiction",
                "platform",
                "application",
                "environment",
            ):
                value = getattr(scope, field)
                if value:
                    stored = IssueRow.payload["scope"][field].astext
                    statement = statement.where(
                        or_(stored.is_(None), func.lower(stored) == value.casefold())
                    )
            for stored_field, boundary, lower in (
                ("effective_to", scope.effective_from, True),
                ("effective_from", scope.effective_to, False),
            ):
                if boundary:
                    stored = cast(IssueRow.payload["scope"][stored_field].astext, Date)
                    statement = statement.where(
                        or_(
                            stored.is_(None),
                            stored >= boundary if lower else stored <= boundary,
                        )
                    )
        rows = await self.session.scalars(statement.limit(min(max(limit, 1), 1000)))
        return [Issue.model_validate(r.payload) for r in rows]

    async def get(self, issue_id: UUID) -> IssueDetail | None:
        row = await self.session.get(IssueRow, issue_id)
        if row is None:
            return None
        events = await self.session.scalars(
            select(IssueEventRow)
            .where(IssueEventRow.issue_id == issue_id)
            .order_by(IssueEventRow.payload["recorded_at"].astext, IssueEventRow.id)
        )
        return IssueDetail(
            issue=Issue.model_validate(row.payload),
            events=tuple(IssueEvent.model_validate(e.payload) for e in events),
        )

    async def _save(self, issue: Issue, event: IssueEvent | None) -> None:
        with tracer.start_as_current_span("issues.upsert"):
            values = dict(
                id=issue.id,
                check_id=issue.check_id,
                status=issue.status,
                issue_type=issue.issue_type,
                payload=issue.model_dump(mode="json"),
            )
            statement = insert(IssueRow).values(**values)
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["id"], set_={k: v for k, v in values.items() if k != "id"}
                )
            )
            if event:
                await self.session.execute(
                    insert(IssueEventRow)
                    .values(id=event.id, issue_id=issue.id, payload=event.model_dump(mode="json"))
                    .on_conflict_do_nothing()
                )

    async def observe(self, issues: tuple[Issue, ...], check_id: str, run_id: UUID) -> int:
        await governance_lock(self.session)
        issues = consolidate(issues)
        rows = await self.session.scalars(select(IssueRow).where(IssueRow.check_id == check_id))
        old = {r.id: Issue.model_validate(r.payload) for r in rows}
        changed = 0
        for issue in issues:
            previous = old.pop(issue.id, None)
            action = "detected"
            if previous:
                material = previous.evidence_revision != issue.evidence_revision
                status = "reopened" if material else previous.status
                issue = issue.model_copy(
                    update={
                        "first_detected": previous.first_detected,
                        "status": status,
                        "owner": previous.owner,
                        "last_observed": now(),
                    }
                )
                action = "reopened" if material else "observed"
            changed += action != "observed"
            count("issues.observations", action=action, check_id=check_id)
            event = IssueEvent(
                id=stable_id(run_id, issue.id, action),
                issue_id=issue.id,
                action=action,
                actor="rule:" + check_id,
                rationale="Evidence-backed condition " + action,
                run_id=run_id,
                evidence_revision=issue.evidence_revision,
            )
            await self._save(issue, event)
        decisions = tuple(
            Decision.model_validate(r.payload)
            for r in await self.session.scalars(select(DecisionRow))
        )
        accepted = {
            d.finding_id: d for d in decision_heads(decisions).values() if d.outcome == "accepted"
        }
        for issue in old.values():
            if not issue.observed:
                continue
            linked = [accepted[f] for f in issue.finding_ids if f in accepted]
            close = bool(linked) and issue.status in {"open", "under_review", "reopened"}
            updated = issue.model_copy(
                update={
                    "observed": False,
                    "status": "resolved" if close else issue.status,
                    "decision_ids": tuple(d.id for d in linked) or issue.decision_ids,
                }
            )
            await self._save(
                updated,
                IssueEvent(
                    id=stable_id(run_id, issue.id, "no_longer_observed"),
                    issue_id=issue.id,
                    action="resolved_by_decision" if close else "no_longer_observed",
                    actor="rule:" + check_id,
                    rationale="Accepted reconciliation cleared the ambiguity."
                    if close
                    else "Condition disappeared; this is not a human resolution.",
                    run_id=run_id,
                    evidence_revision=issue.evidence_revision,
                ),
            )
        return changed

    async def review(
        self,
        issue_id: UUID,
        status: IssueStatus,
        actor: str,
        rationale: str,
        owner: str | None = None,
    ) -> IssueDetail:
        await governance_lock(self.session)
        detail = await self.get(issue_id)
        if detail is None:
            raise ValueError("Unknown issue")
        event = IssueEvent(
            issue_id=issue_id,
            action=status,
            actor=actor,
            rationale=rationale,
            evidence_revision=detail.issue.evidence_revision,
        )
        await self._save(
            detail.issue.model_copy(
                update={
                    "status": status,
                    "owner": owner if owner is not None else detail.issue.owner,
                }
            ),
            event,
        )
        result = await self.get(issue_id)
        assert result is not None
        return result
