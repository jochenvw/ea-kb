from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ekb.adapters.persistence.postgres.models import SourceRow
from ekb.domain.models import SourceRecord
from ekb.domain.sources import SourceGroup, source_groups


async def source_group(session: AsyncSession, group_id: UUID) -> SourceGroup | None:
    payloads = await session.scalars(
        select(SourceRow.payload).where(
            SourceRow.payload["item"]["metadata"]["benchmark_edition"].astext.is_not(None)
        )
    )
    return next(
        (
            group
            for group in source_groups(SourceRecord.model_validate(payload) for payload in payloads)
            if group.id == group_id
        ),
        None,
    )


async def source_ids(session: AsyncSession, source_id: UUID) -> list[UUID]:
    if await session.get(SourceRow, source_id) is not None:
        return [source_id]
    group = await source_group(session, source_id)
    return [document.item.id for document in group.documents] if group else [source_id]
