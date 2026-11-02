from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ekb.adapters.persistence.postgres.models import (
    ConceptRow,
    SolutionArchitectureControlRow,
    SolutionArchitectureRevisionRow,
    SolutionArchitectureRow,
)
from ekb.domain.architectures import (
    ArchitectureControl,
    SolutionArchitecture,
    SolutionArchitectureRevision,
)
from ekb.domain.models import stable_id


class Architectures:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, revision: SolutionArchitectureRevision) -> SolutionArchitecture:
        existing = await self.session.scalar(
            select(SolutionArchitectureRevisionRow).where(
                SolutionArchitectureRevisionRow.architecture_id == revision.architecture_id,
                SolutionArchitectureRevisionRow.version == revision.version,
            )
        )
        if existing is not None:
            if existing.id != revision.id:
                raise ValueError(
                    "Architecture version already exists with different content or controls"
                )
            architecture = await self.get(revision.architecture_id)
            assert architecture is not None
            return architecture
        concept_count = await self.session.scalar(
            select(func.count())
            .select_from(ConceptRow)
            .where(ConceptRow.id.in_(control.concept_id for control in revision.controls))
        )
        if concept_count != len(revision.controls):
            raise ValueError("Architecture controls must reference stored concepts")
        await self.session.execute(
            insert(SolutionArchitectureRow)
            .values(id=revision.architecture_id, created_at=revision.recorded_at)
            .on_conflict_do_nothing()
        )
        await self.session.execute(
            insert(SolutionArchitectureRevisionRow).values(
                id=revision.id,
                architecture_id=revision.architecture_id,
                version=revision.version,
                content_hash=revision.content_hash,
                recorded_at=revision.recorded_at,
                payload=revision.model_dump(mode="json", exclude={"controls"}),
            )
        )
        await self.session.execute(
            insert(SolutionArchitectureControlRow),
            [
                {
                    "id": stable_id(revision.id, control.concept_id),
                    "revision_id": revision.id,
                    "concept_id": control.concept_id,
                    "disposition": control.disposition.value,
                    "payload": control.model_dump(mode="json"),
                }
                for control in revision.controls
            ],
        )
        architecture = await self.get(revision.architecture_id)
        assert architecture is not None
        return architecture

    async def list(
        self, limit: int = 50, *, after: UUID | None = None
    ) -> list[SolutionArchitecture]:
        query = select(SolutionArchitectureRow.id).order_by(SolutionArchitectureRow.id)
        if after is not None:
            query = query.where(SolutionArchitectureRow.id > after)
        ids = list(await self.session.scalars(query.limit(limit)))
        records = [await self.get(architecture_id) for architecture_id in ids]
        return [record for record in records if record is not None]

    async def get(self, architecture_id: UUID) -> SolutionArchitecture | None:
        if await self.session.get(SolutionArchitectureRow, architecture_id) is None:
            return None
        rows = list(
            await self.session.scalars(
                select(SolutionArchitectureRevisionRow)
                .where(SolutionArchitectureRevisionRow.architecture_id == architecture_id)
                .order_by(
                    SolutionArchitectureRevisionRow.recorded_at,
                    SolutionArchitectureRevisionRow.id,
                )
            )
        )
        controls = await self._controls([row.id for row in rows])
        return SolutionArchitecture(
            id=architecture_id,
            revisions=tuple(
                SolutionArchitectureRevision.model_validate(
                    {
                        **row.payload,
                        "controls": [
                            control.model_dump(mode="json") for control in controls.get(row.id, ())
                        ],
                    }
                )
                for row in rows
            ),
        )

    async def revision(self, revision_id: UUID) -> SolutionArchitectureRevision | None:
        row = await self.session.get(SolutionArchitectureRevisionRow, revision_id)
        if row is None:
            return None
        controls = await self._controls([revision_id])
        return SolutionArchitectureRevision.model_validate(
            {
                **row.payload,
                "controls": [
                    control.model_dump(mode="json") for control in controls.get(revision_id, ())
                ],
            }
        )

    async def _controls(
        self, revision_ids: list[UUID]
    ) -> dict[UUID, tuple[ArchitectureControl, ...]]:
        if not revision_ids:
            return {}
        rows = list(
            await self.session.scalars(
                select(SolutionArchitectureControlRow)
                .where(SolutionArchitectureControlRow.revision_id.in_(revision_ids))
                .order_by(
                    SolutionArchitectureControlRow.revision_id,
                    SolutionArchitectureControlRow.concept_id,
                )
            )
        )
        grouped: dict[UUID, list[ArchitectureControl]] = {
            revision_id: [] for revision_id in revision_ids
        }
        for row in rows:
            grouped[row.revision_id].append(ArchitectureControl.model_validate(row.payload))
        return {revision_id: tuple(values) for revision_id, values in grouped.items()}
