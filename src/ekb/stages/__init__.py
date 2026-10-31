from __future__ import annotations

import asyncio

from ekb.domain.execution import ArtifactReference, CheckOptions, Execution, ProcessorSpec
from ekb.domain.issues import consolidate
from ekb.domain.models import fingerprint, now
from ekb.domain.resolution import Decision, Finding, decision_heads
from ekb.observability import log, tracer
from ekb.observability.metrics import count
from ekb.ports import UnitOfWorkFactory
from ekb.ports.workflow import CheckContext, KnowledgeCheck, StageContext


def _check_cache_key(
    check: KnowledgeCheck,
    options: CheckOptions,
    input_revision: str,
    prior_revisions: list[str],
) -> str:
    return fingerprint(
        spec=check.spec.model_dump(),
        options=options.model_dump(),
        input=input_revision,
        prior=prior_revisions,
    )


class CheckedStage:
    def __init__(
        self, database: UnitOfWorkFactory, spec: ProcessorSpec, checks: tuple[KnowledgeCheck, ...]
    ):
        self.database, self.spec, self.checks = database, spec, checks

    async def execute(self, context: StageContext) -> ArtifactReference:
        async with self.database() as uow:
            snapshot = await uow.resolution.snapshot()
        input_revision = fingerprint(
            source=snapshot.revision,
            governance=snapshot.governance_revision if self.spec.id == "issues" else None,
            upstream=[i.revision for i in context.inputs],
        )
        record_ids = []
        prior_findings: list[Finding] = []
        prior_revisions: list[str] = []
        for check in self.checks:
            options = context.configuration.checks.get(check.spec.id, CheckOptions())
            if not options.enabled:
                continue
            cache_key = _check_cache_key(check, options, input_revision, prior_revisions)
            execution = Execution(
                parent_id=context.execution_id,
                kind="check",
                processor_id=f"{self.spec.id}/{check.spec.id}",
                version=check.spec.version,
                fingerprint=cache_key,
                input_revision=input_revision,
                owner=context.owner,
            )
            async with self.database() as uow:
                cached = await uow.workflow.successful(execution.processor_id, cache_key)
            if cached and cached.output and self.spec.id != "indexing":
                record_ids.extend(cached.output.record_ids)
                prior_revisions.append(cached.output.revision)
                active_ids = set(cached.output.record_ids)
                prior_findings.extend(
                    finding for finding in snapshot.findings if finding.id in active_ids
                )
                execution = execution.model_copy(
                    update={
                        "status": "skipped",
                        "output": cached.output,
                        "finished_at": now(),
                    }
                )
                async with self.database() as uow:
                    await uow.workflow.save_execution(execution)
                    await uow.commit()
                continue
            async with self.database() as uow:
                await uow.workflow.save_execution(execution)
                await uow.commit()
            try:
                with tracer.start_as_current_span(
                    "pipeline.check.execute",
                    attributes={
                        "pipeline.run.id": str(context.run_id),
                        "check.id": check.spec.id,
                        "check.version": check.spec.version,
                        "input.revision": input_revision,
                    },
                ):
                    check_context = CheckContext(
                        run_id=context.run_id,
                        input_revision=snapshot.revision,
                        snapshot=snapshot,
                        options=options,
                        stage_configuration=context.configuration,
                        check_version=check.spec.version,
                        prior_findings=tuple(prior_findings),
                    )
                    result = await check.evaluate(check_context)
                    result = result.model_copy(update={"issues": consolidate(result.issues)})
                    count("pipeline.check.executions", check_id=check.spec.id, status="done")
                    count("pipeline.findings", len(result.findings), check_id=check.spec.id)
                    async with self.database() as uow:
                        if not await uow.workflow.renew("knowledge-pipeline", context.owner):
                            raise RuntimeError("Pipeline execution lost its lease")
                        for finding in result.findings:
                            await uow.resolution.save_finding(finding)
                        if self.spec.id == "issues":
                            await uow.issues.observe(result.issues, check.spec.id, context.run_id)
                        heads = decision_heads(snapshot.decisions)
                        for finding in result.findings:
                            if (
                                options.auto_accept_exact
                                and finding.deterministic
                                and finding.action == "recognize_identity"
                                and finding.id not in heads
                            ):
                                await uow.resolution.apply_decision(
                                    Decision(
                                        finding_id=finding.id,
                                        outcome="accepted",
                                        actor="rule:exact-identity",
                                        actor_kind="rule",
                                        policy="exact-identity-v1",
                                        rationale="Explicit policy recognizes an exact identity.",
                                    )
                                )
                        ids = tuple(f.id for f in result.findings) + tuple(
                            i.id for i in result.issues
                        )
                        output = ArtifactReference(
                            contract=check.spec.output_contract,
                            revision=fingerprint(records=sorted(str(i) for i in ids)),
                            source_revision=snapshot.revision,
                            producer_fingerprint=cache_key,
                            record_ids=ids,
                        )
                        execution = execution.model_copy(
                            update={
                                "status": "done",
                                "output": output,
                                "finished_at": now(),
                                "statistics": {
                                    "candidates": result.candidates,
                                    "findings": len(result.findings),
                                    "issues": len(result.issues),
                                    "model_calls": result.model_calls,
                                    "embedding_calls": result.embedding_calls,
                                    "input_tokens": result.usage.input_tokens,
                                    "output_tokens": result.usage.output_tokens,
                                    "model_latency_ms": result.usage.latency_ms,
                                    **(
                                        {"model_cost": result.usage.cost}
                                        if result.usage.cost is not None
                                        else {}
                                    ),
                                    **result.statistics,
                                },
                            }
                        )
                        await uow.workflow.save_execution(execution)
                        await uow.commit()
                    record_ids.extend(ids)
                    prior_revisions.append(output.revision)
                    prior_findings.extend(result.findings)
                    log.info(
                        "pipeline_check_done",
                        check_id=check.spec.id,
                        version=check.spec.version,
                        **execution.statistics,
                    )
            except BaseException as error:

                async def fail(
                    failed: Execution = execution, failure: BaseException = error
                ) -> None:
                    async with self.database() as uow:
                        await uow.workflow.save_execution(
                            failed.model_copy(
                                update={
                                    "status": "cancelled"
                                    if isinstance(failure, asyncio.CancelledError)
                                    else "failed",
                                    "error": type(failure).__name__,
                                    "finished_at": now(),
                                }
                            )
                        )
                        await uow.commit()

                await asyncio.shield(fail())
                raise
        return ArtifactReference(
            contract=self.spec.output_contract,
            revision=fingerprint(input=input_revision, records=sorted(str(i) for i in record_ids)),
            source_revision=snapshot.revision,
            producer_fingerprint="",
            record_ids=tuple(record_ids),
        )
