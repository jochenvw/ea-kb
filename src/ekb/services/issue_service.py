from uuid import UUID

from ekb.domain.issues import Issue, IssueDetail, IssueStatus
from ekb.domain.models import Scope
from ekb.ports import UnitOfWorkFactory


class IssueService:
    def __init__(self, database: UnitOfWorkFactory):
        self.database = database

    async def list(
        self,
        status: IssueStatus | None = None,
        issue_type: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        scope: Scope | None = None,
    ) -> list[Issue]:
        async with self.database() as uow:
            return await uow.issues.list(status, issue_type, severity, limit, scope)

    async def get(self, issue_id: UUID) -> IssueDetail | None:
        async with self.database() as uow:
            return await uow.issues.get(issue_id)

    async def review(
        self,
        issue_id: UUID,
        status: IssueStatus,
        actor: str,
        rationale: str,
        owner: str | None = None,
    ) -> IssueDetail:
        async with self.database() as uow:
            result = await uow.issues.review(issue_id, status, actor, rationale, owner)
            await uow.commit()
        return result
