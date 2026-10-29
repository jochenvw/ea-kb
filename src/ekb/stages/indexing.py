from ekb.domain.execution import ArtifactReference, ProcessorSpec
from ekb.ports import UnitOfWorkFactory
from ekb.ports.workflow import IndexOperation, KnowledgeCheck, StageContext
from ekb.stages import CheckedStage
from ekb.stages.enrichment import Check


class IndexingStage:
    spec = ProcessorSpec(
        id="indexing",
        name="Incremental source indexing",
        version="1",
        input_contract="sources/v1",
        output_contract="indexed/v1",
    )

    def __init__(self, database: UnitOfWorkFactory, index: IndexOperation, version: str = "1"):
        self.database, self.index = database, index
        self.spec = self.spec.model_copy(update={"version": version})
        self.checks: tuple[KnowledgeCheck, ...] = (Check("source_ingestion", index, version),)

    async def execute(self, context: StageContext) -> ArtifactReference:
        await CheckedStage(self.database, self.spec, self.checks).execute(context)
        async with self.database() as uow:
            snapshot = await uow.resolution.snapshot()
        return ArtifactReference(
            contract=self.spec.output_contract,
            revision=snapshot.revision,
            source_revision=snapshot.revision,
            producer_fingerprint="",
        )
