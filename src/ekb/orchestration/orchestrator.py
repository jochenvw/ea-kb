from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from uuid import UUID, uuid4

from ekb.domain.execution import (
    ArtifactReference,
    Execution,
    PipelineConfiguration,
    PipelineStatus,
    StageOptions,
)
from ekb.domain.models import fingerprint, now
from ekb.observability import log, tracer
from ekb.observability.metrics import count
from ekb.orchestration.registry import StageRegistry
from ekb.ports import UnitOfWorkFactory
from ekb.ports.workflow import StageContext


class PipelineOrchestrator:
    def __init__(
        self,
        database: UnitOfWorkFactory,
        registry: StageRegistry,
        progress: Callable[[Execution], None] | None = None,
    ):
        self.database, self.registry = database, registry
        self.progress = progress

    async def status(self, run_id: UUID | None = None) -> PipelineStatus | None:
        async with self.database() as uow:
            runs = await uow.workflow.executions(limit=1) if run_id is None else []
            run = await uow.workflow.execution(run_id) if run_id else next(iter(runs), None)
            if run is None or run.kind != "pipeline":
                return None
            stages = await uow.workflow.executions(run.id, 1000)
            checks = [
                check for stage in stages for check in await uow.workflow.executions(stage.id, 1000)
            ]
        return PipelineStatus(run=run, executions=tuple([*stages, *checks]))

    async def _save(self, execution: Execution) -> None:
        async with self.database() as uow:
            await uow.workflow.save_execution(execution)
            await uow.commit()
        if self.progress:
            self.progress(execution)

    async def _heartbeat(self, owner: UUID) -> None:
        while True:
            await asyncio.sleep(20)
            async with self.database() as uow:
                if not await uow.workflow.lease("knowledge-pipeline", owner):
                    raise RuntimeError("Pipeline lease was lost")
                await uow.commit()

    async def run(
        self,
        stages: tuple[str, ...] | None = None,
        configuration: PipelineConfiguration | None = None,
        resume: UUID | None = None,
    ) -> PipelineStatus:
        owner = uuid4()
        async with self.database() as uow:
            previous = await uow.workflow.execution(resume) if resume else None
            if resume and (previous is None or previous.kind != "pipeline"):
                raise ValueError("Unknown pipeline run")
            if previous and previous.status == "done":
                result = await self.status(previous.id)
                assert result is not None
                return result
            config = configuration or (previous.configuration if previous else None)
            config = config or PipelineConfiguration()
            selected = stages or (
                previous.selected_stages if previous else tuple(self.registry.stages)
            )
            self.registry.validate(config)
            ordered = self.registry.order(selected)
            if not await uow.workflow.lease("knowledge-pipeline", owner):
                raise ValueError("Another pipeline owns the renewable execution lease")
            await uow.commit()
        run = Execution(
            id=previous.id if previous else uuid4(),
            kind="pipeline",
            owner=owner,
            processor_id="pipeline",
            version="1",
            fingerprint=fingerprint(stages=[self.registry.fingerprint(s, config) for s in ordered]),
            configuration=config,
            selected_stages=ordered,
            attempts=previous.attempts + 1 if previous else 1,
        )
        heartbeat = asyncio.create_task(self._heartbeat(owner))
        try:
            await self._save(run)
            if previous:
                await self._interrupt_previous(previous.id, owner)
            with tracer.start_as_current_span(
                "pipeline.run", attributes={"pipeline.run.id": str(run.id)}
            ):
                async with asyncio.TaskGroup() as group:
                    # A lost lease cancels stage work, rather than allowing a stale writer.
                    group.create_task(self._watch_heartbeat(heartbeat))
                    work = group.create_task(self._stages(run, config, ordered))
                    try:
                        await work
                    finally:
                        heartbeat.cancel()
            run = run.model_copy(update={"status": "done", "finished_at": now()})
            count("pipeline.runs", status="done")
        except BaseException as error:
            run = run.model_copy(
                update={
                    "status": "cancelled"
                    if isinstance(error, asyncio.CancelledError)
                    else "failed",
                    "error": type(error).__name__,
                    "finished_at": now(),
                }
            )
            raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat

            async def finish() -> None:
                async with self.database() as uow:
                    if not await uow.workflow.lease("knowledge-pipeline", owner):
                        return
                    await uow.workflow.save_execution(run)
                    await uow.workflow.release("knowledge-pipeline", owner)
                    await uow.commit()

            await asyncio.shield(finish())
        result = await self.status(run.id)
        assert result is not None
        return result

    async def _watch_heartbeat(self, heartbeat: asyncio.Task[None]) -> None:
        with suppress(asyncio.CancelledError):
            await heartbeat

    async def _interrupt_previous(self, run_id: UUID, owner: UUID) -> None:
        async with self.database() as uow:
            stages = await uow.workflow.executions(run_id, 1000)
            records = [*stages]
            for stage in stages:
                records.extend(await uow.workflow.executions(stage.id, 1000))
            for record in records:
                if record.status == "running":
                    await uow.workflow.save_execution(
                        record.model_copy(
                            update={
                                "status": "cancelled",
                                "error": "InterruptedAttempt",
                                "owner": owner,
                                "finished_at": now(),
                            }
                        )
                    )
            await uow.commit()

    async def _stages(
        self, run: Execution, config: PipelineConfiguration, selected: tuple[str, ...]
    ) -> None:
        assert run.owner is not None
        for identifier in selected:
            stage = self.registry.stages[identifier]
            options = config.stages.get(identifier, StageOptions())
            manifest = self.registry.fingerprint(identifier, config)
            execution = Execution(
                parent_id=run.id,
                kind="stage",
                processor_id=identifier,
                version=stage.spec.version,
                fingerprint=manifest,
                owner=run.owner,
            )
            if not options.enabled:
                await self._save(
                    execution.model_copy(
                        update={
                            "status": "skipped",
                            "finished_at": now(),
                        }
                    )
                )
                continue
            async with self.database() as uow:
                before = await uow.knowledge.counts()
                snapshot = await uow.resolution.snapshot()
                inputs: list[ArtifactReference] = []
                for dependency in stage.spec.dependencies:
                    upstream = await uow.workflow.successful(dependency)
                    expected = self.registry.fingerprint(dependency, config)
                    if (
                        not upstream
                        or not upstream.output
                        or upstream.output.source_revision != snapshot.revision
                        or upstream.output.producer_fingerprint != expected
                        or upstream.output.contract != stage.spec.input_contract
                    ):
                        raise ValueError(
                            f"{identifier} requires current compatible {dependency} output"
                        )
                    inputs.append(upstream.output)
            execution = execution.model_copy(update={"input_revision": snapshot.revision})
            await self._save(execution)
            try:
                with tracer.start_as_current_span(
                    "pipeline.stage.execute",
                    attributes={
                        "pipeline.run.id": str(run.id),
                        "stage.id": identifier,
                        "stage.version": stage.spec.version,
                        "input.revision": snapshot.revision,
                    },
                ):
                    attempts = 0
                    while True:
                        try:
                            output = await stage.execute(
                                StageContext(
                                    run_id=run.id,
                                    execution_id=execution.id,
                                    owner=run.owner,
                                    inputs=tuple(inputs),
                                    configuration=options,
                                )
                            )
                            break
                        except (TimeoutError, ConnectionError):
                            if attempts >= config.retries:
                                raise
                            attempts += 1
                            log.warning("pipeline_retry", stage_id=identifier, attempt=attempts)
                            await asyncio.sleep(0.1 * 2**attempts)
                    output = output.model_copy(update={"producer_fingerprint": manifest})
                async with self.database() as uow:
                    after = await uow.knowledge.counts()
                    checks = await uow.workflow.executions(execution.id, 1000)
                execution = execution.model_copy(
                    update={
                        "status": "done",
                        "output": output,
                        "finished_at": now(),
                        "attempts": attempts + 1,
                        "statistics": {
                            "new_concepts": after.concepts - before.concepts,
                            "new_proposals": after.proposals - before.proposals,
                            "new_issues": after.issues - before.issues,
                            "open_issues": after.open_issues,
                            "checks_done": sum(c.status == "done" for c in checks),
                            "checks_reused": sum(c.status == "skipped" for c in checks),
                        },
                    }
                )
            except BaseException as error:
                execution = execution.model_copy(
                    update={
                        "status": "cancelled"
                        if isinstance(error, asyncio.CancelledError)
                        else "failed",
                        "error": type(error).__name__,
                        "finished_at": now(),
                    }
                )
                raise
            finally:
                await asyncio.shield(self._save(execution))
