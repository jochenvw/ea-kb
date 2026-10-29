from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import UUID, uuid4

from ekb.adapters.sources import SourceMissingError
from ekb.domain.benchmarks import (
    BenchmarkAsset,
    NormalizedBenchmarkDocument,
    StoredBenchmarkDocument,
)
from ekb.domain.grounding import validate_evidence
from ekb.domain.models import (
    ProcessingTask,
    ScanReport,
    Segment,
    SourceItem,
    SourceRecord,
    SourceRevision,
    Usage,
    content_hash,
    fingerprint,
    now,
    parsing_fingerprint,
    stable_id,
)
from ekb.domain.publication import assess_publication, publication_fingerprint
from ekb.observability import log, tracer
from ekb.ports import (
    ArtifactStore,
    DocumentParser,
    InferencePort,
    SourceConnector,
    UnitOfWorkFactory,
)
from ekb.services.benchmarks import BENCHMARK_EXTRACTOR_VERSION, benchmark_extraction
from ekb.services.retry import retry


class IngestionPipeline:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        artifacts: ArtifactStore,
        parser: DocumentParser,
        inference: InferencePort,
        *,
        concurrency: int = 4,
        parse_concurrency: int = 2,
        model_concurrency: int = 2,
        inference_config: str = "",
        progress: Callable[[SourceItem, str], None] | None = None,
    ):
        self.uow = uow
        self.artifacts = artifacts
        self.parser = parser
        self.inference = inference
        self.concurrency = concurrency
        self.parse_gate = asyncio.Semaphore(parse_concurrency)
        self.model_gate = asyncio.Semaphore(model_concurrency)
        self.inference_fingerprint = fingerprint(
            schema="concept-v1",
            model=inference.capabilities.model_dump(),
            prompt=inference.prompt_version,
            config=inference_config,
        )
        self.progress = progress or (lambda item, status: None)

    def _processing_fingerprint(self, content: str, item: SourceItem) -> str:
        if "benchmark_asset" in item.metadata:
            asset = BenchmarkAsset.model_validate(item.metadata["benchmark_asset"])
            return fingerprint(
                content=content,
                parser=asset.parser_version,
                normalized=asset.normalized_sha256,
                extractor=BENCHMARK_EXTRACTOR_VERSION,
            )
        return fingerprint(
            content=content,
            parser=parsing_fingerprint(self.parser.version, item),
            inference=self.inference_fingerprint,
            publication=publication_fingerprint(
                item, parsing_fingerprint(self.parser.version, item)
            ),
        )

    async def scan(self, connectors: list[SourceConnector], *, dry_run: bool = False) -> ScanReport:
        report = ScanReport(id=uuid4(), dry_run=dry_run)

        def increment(**counts: int) -> None:
            nonlocal report
            report = report.model_copy(
                update={key: getattr(report, key) + amount for key, amount in counts.items()}
            )

        def failure(label: str, error: Exception) -> None:
            nonlocal report
            increment(failed=1)
            # Only identifiers and exception types enter logs/reports; not document text or URLs.
            report = report.model_copy(
                update={"errors": [*report.errors, f"{label}: {type(error).__name__}"]}
            )
            log.error("source_failed", source_id=label, error_type=type(error).__name__)

        async def save_report() -> None:
            if not dry_run:
                async with self.uow() as uow:
                    await uow.processing.save_scan(report)
                    await uow.commit()

        queue: asyncio.Queue[tuple[SourceConnector, SourceItem] | None] = asyncio.Queue(
            maxsize=self.concurrency * 2
        )
        discovery_gate = asyncio.Semaphore(self.concurrency)

        async def discover(connector: SourceConnector) -> None:
            seen: set[UUID] = set()
            try:
                async with discovery_gate:
                    with tracer.start_as_current_span(
                        "source.discovery", attributes={"connector": connector.name}
                    ):
                        async for item in connector.discover():
                            if item.id in seen:
                                continue
                            seen.add(item.id)
                            increment(discovered=1)
                            await queue.put((connector, item))
                    if not dry_run:
                        async with self.uow() as uow:
                            for source in await uow.sources.list():
                                if (
                                    source.item.connector == connector.name
                                    and source.item.id not in seen
                                ):
                                    await uow.sources.save(
                                        source.model_copy(
                                            update={"status": "missing", "checked_at": now()}
                                        )
                                    )
                                    increment(missing=1)
                            await uow.commit()
            except Exception as error:
                failure("discovery", error)

        async def producer() -> None:
            async with asyncio.TaskGroup() as group:
                for connector in connectors:
                    group.create_task(discover(connector))
            for _ in range(self.concurrency):
                await queue.put(None)

        async def worker() -> None:
            while (work := await queue.get()) is not None:
                connector, item = work
                self.progress(item, "checking")
                try:
                    outcome, concepts = await self._process(connector, item, dry_run)
                    increment(**{outcome: 1}, concepts=concepts)
                    self.progress(item, outcome)
                except Exception as error:
                    failure(str(item.id), error)
                    self.progress(item, "failed")
                await save_report()

        await save_report()
        with tracer.start_as_current_span("scan", attributes={"scan.id": str(report.id)}):
            try:
                async with asyncio.TaskGroup() as group:
                    group.create_task(producer())
                    for _ in range(self.concurrency):
                        group.create_task(worker())
                report = report.model_copy(
                    update={"status": "completed" if report.failed == 0 else "partial"}
                )
            except asyncio.CancelledError:
                report = report.model_copy(update={"status": "cancelled"})
                raise
            finally:
                report = report.model_copy(update={"finished_at": now()})
                await asyncio.shield(save_report())
        return report

    async def _process(
        self, connector: SourceConnector, item: SourceItem, dry_run: bool
    ) -> tuple[str, int]:
        if item.metadata.get("metadata_only"):
            if not dry_run:
                async with self.uow() as uow:
                    await uow.sources.save(SourceRecord(item=item, status="metadata_only"))
                    await uow.commit()
            return "skipped", 0
        with tracer.start_as_current_span(
            "revision.comparison", attributes={"source.id": str(item.id)}
        ):
            async with self.uow() as uow:
                previous = await uow.sources.get(item.id)
                revision = (
                    await uow.sources.revision(previous.revision_id)
                    if previous and previous.revision_id
                    else None
                )
                same_metadata = bool(
                    previous
                    and previous.status == "available"
                    and revision
                    and item.indicator
                    and item.indicator == previous.item.indicator
                )
                task = (
                    await uow.processing.task(
                        revision.id, self._processing_fingerprint(revision.content_hash, item)
                    )
                    if revision
                    else None
                )
            if dry_run:
                return (
                    ("skipped", 0)
                    if same_metadata and task and task.status == "done"
                    else ("processed", 0)
                )
            if same_metadata and task and task.status == "done":
                async with self.uow() as uow:
                    assert previous is not None
                    await uow.sources.save(
                        previous.model_copy(
                            update={
                                "item": item,
                                "status": "available",
                                "checked_at": now(),
                                "error": None,
                            }
                        )
                    )
                    await uow.commit()
                log.info(
                    "source_skipped", source_id=str(item.id), reason="metadata_and_fingerprint"
                )
                return "skipped", 0

        content = None
        source = previous or SourceRecord(item=item)
        if not same_metadata:
            try:
                with tracer.start_as_current_span("document.download"):
                    fetched = await retry(lambda: connector.fetch(item, previous))
            except Exception as error:
                status = "missing" if isinstance(error, SourceMissingError) else "inaccessible"
                async with self.uow() as uow:
                    await uow.sources.save(
                        source.model_copy(
                            update={
                                "item": item,
                                "status": status,
                                "checked_at": now(),
                                "error": type(error).__name__,
                            }
                        )
                    )
                    await uow.commit()
                raise
            source = source.model_copy(
                update={
                    "item": item,
                    "etag": fetched.etag,
                    "last_modified": fetched.last_modified,
                    "status": "available",
                    "checked_at": now(),
                    "error": None,
                }
            )
            if not fetched.not_modified:
                assert fetched.content is not None
                content = fetched.content
                digest = content_hash(content)
                key = await self.artifacts.put(content)
                revision = SourceRevision(
                    id=stable_id(item.id, digest),
                    source_id=item.id,
                    content_hash=digest,
                    artifact_key=key,
                    predecessor_id=revision.id
                    if revision and revision.content_hash != digest
                    else None,
                    source_location=item.location,
                    metadata_snapshot=item.model_dump(mode="json"),
                )
        assert revision is not None
        async with self.uow() as uow:
            await uow.sources.save(source)
            revision = await uow.sources.save_revision(revision)
            await uow.sources.save(source.model_copy(update={"revision_id": revision.id}))
            await uow.commit()
        process_fp = self._processing_fingerprint(revision.content_hash, item)
        task = ProcessingTask(
            id=stable_id(revision.id, process_fp), revision_id=revision.id, fingerprint=process_fp
        )
        owner = uuid4()
        async with self.uow() as uow:
            claimed = await uow.processing.claim(task, owner)
            await uow.commit()
        if not claimed:
            return "skipped", 0
        stop = asyncio.Event()
        try:
            with tracer.start_as_current_span(
                "processing",
                attributes={"revision.id": str(revision.id), "processing.task.id": str(task.id)},
            ):
                async with asyncio.TaskGroup() as group:
                    group.create_task(self._heartbeat(task.id, owner, stop))
                    try:
                        concepts, usage = await self._execute(
                            item, revision, content, owner, task.id
                        )
                    finally:
                        stop.set()
            task = task.model_copy(update={"status": "done", "usage": usage})
        except BaseException as error:
            task = task.model_copy(
                update={
                    "status": "cancelled"
                    if isinstance(error, asyncio.CancelledError)
                    else "failed",
                    "error": type(error).__name__,
                }
            )
            raise
        finally:

            async def finish() -> None:
                async with self.uow() as uow:
                    await uow.processing.finish(task, owner)
                    await uow.commit()

            await asyncio.shield(finish())
        return "processed", concepts

    async def _heartbeat(self, task_id: UUID, owner: UUID, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=30)
                return
            except TimeoutError:
                pass
            async with self.uow() as uow:
                await uow.processing.heartbeat(task_id, owner)
                await uow.commit()

    async def _execute(
        self,
        item: SourceItem,
        revision: SourceRevision,
        content: bytes | None,
        owner: UUID,
        task_id: UUID,
    ) -> tuple[int, Usage]:
        if "benchmark_asset" in item.metadata:
            return await self._execute_benchmark(item, revision, owner, task_id)
        async with self.uow() as uow:
            segments = await uow.sources.segments(
                revision.id, parsing_fingerprint(self.parser.version, item)
            )
        if segments is None:
            if content is None:
                content = await self.artifacts.get(revision.artifact_key)
            async with self.parse_gate:
                self.progress(item, "parsing")
                with tracer.start_as_current_span("parsing"):
                    segments = await self.parser.parse(item, revision, content)
            async with self.uow() as uow:
                await uow.processing.heartbeat(task_id, owner)
                await uow.sources.save_segments(
                    revision.id, segments, parsing_fingerprint(self.parser.version, item)
                )
                await uow.commit()
        count = 0
        publication = assess_publication(item, segments)
        async with self.uow() as uow:
            await uow.processing.heartbeat(task_id, owner)
            await uow.sources.save_publication(
                revision.id,
                publication,
                publication_fingerprint(item, parsing_fingerprint(self.parser.version, item)),
            )
            await uow.commit()
        usages: list[Usage] = []
        for segment in segments:
            if segment.quality in {"image_only", "ocr_no_text", "document_metadata"}:
                log.info("segment_skipped", segment_id=str(segment.id), reason=segment.quality)
                continue
            cache_fp = fingerprint(
                inference=self.inference_fingerprint,
                source=item.id,
                text=segment.text,
                heading=segment.heading,
                control=segment.location.get("control_id"),
                part=segment.location.get("part_id"),
                structured=segment.location.get("structured_control"),
            )
            async with self.uow() as uow:
                result = await uow.processing.cached(segment.id, cache_fp)
            if result is None:
                async with self.model_gate:
                    self.progress(item, f"extracting segment {segment.ordinal + 1}/{len(segments)}")
                    with tracer.start_as_current_span(
                        "semantic.extraction",
                        attributes={
                            "segment.id": str(segment.id),
                            "model.id": self.inference.capabilities.model_id,
                            "prompt.version": self.inference.prompt_version,
                        },
                    ):
                        result = await self.inference.extract(segment)
                with tracer.start_as_current_span("schema.validation"):
                    validate_evidence(result.extraction, segment)
                usages.append(result.usage)
                called_model = True
            else:
                log.info("segment_replayed", segment_id=str(segment.id))
                called_model = False
            async with self.uow() as uow:
                await uow.processing.heartbeat(task_id, owner)
                ids = await uow.knowledge.persist(segment, result)
                await uow.processing.cache(segment.id, cache_fp, result)
                if called_model:
                    await uow.processing.accumulate_usage(task_id, owner, result.usage)
                await uow.commit()
            count += len(set(ids))
        cost_values = [u.cost for u in usages if u.cost is not None]
        usage = Usage(
            input_tokens=sum(u.input_tokens for u in usages),
            output_tokens=sum(u.output_tokens for u in usages),
            latency_ms=sum(u.latency_ms for u in usages),
            cost=sum(cost_values) if cost_values else None,
            validation_failures=sum(u.validation_failures for u in usages),
        )
        return count, usage

    async def _execute_benchmark(
        self,
        item: SourceItem,
        revision: SourceRevision,
        owner: UUID,
        task_id: UUID,
    ) -> tuple[int, Usage]:
        asset = BenchmarkAsset.model_validate(item.metadata["benchmark_asset"])
        if not asset.normalized_key or not asset.normalized_sha256 or not asset.retrieved_at:
            raise ValueError("Benchmark ingestion requires an explicitly synchronized asset")
        raw = await self.artifacts.get_named(asset.normalized_key)
        if content_hash(raw) != asset.normalized_sha256:
            raise ValueError("Normalized benchmark document failed its integrity check")
        document = await asyncio.to_thread(NormalizedBenchmarkDocument.model_validate_json, raw)
        if (
            document.asset_id != item.id
            or document.content_hash != revision.content_hash
            or document.parser_version != asset.parser_version
        ):
            raise ValueError("Normalized benchmark identity does not match the stored original")
        segment = Segment(
            id=document.segment_id,
            revision_id=revision.id,
            ordinal=0,
            text=document.text,
            heading=document.title,
            quality="structured_benchmark",
            location={"benchmark_edition": asset.edition, "role": asset.role},
        )
        result, keys, unmaterialized = benchmark_extraction(document)
        self.progress(item, "persisting structured benchmark evidence")
        with tracer.start_as_current_span(
            "benchmark.persistence",
            attributes={
                "benchmark.edition": asset.edition,
                "benchmark.controls": len(document.controls),
            },
        ):
            async with self.uow() as uow:
                await uow.processing.heartbeat(task_id, owner)
                await uow.sources.save_segments(revision.id, [segment], document.parser_version)
                await uow.sources.save_publication(
                    revision.id,
                    document.publication,
                    fingerprint(parser=document.parser_version, normalized=asset.normalized_sha256),
                )
                ids = await uow.knowledge.persist(segment, result)
                await uow.benchmarks.save(
                    StoredBenchmarkDocument(
                        id=stable_id(document.segment_id, BENCHMARK_EXTRACTOR_VERSION),
                        source_id=item.id,
                        revision_id=revision.id,
                        edition=asset.edition,
                        role=asset.role,
                        retrieved_at=asset.retrieved_at,
                        extractor_version=BENCHMARK_EXTRACTOR_VERSION,
                        document=document,
                        concept_ids=dict(zip(keys, ids, strict=True)),
                        unmaterialized_controls=unmaterialized,
                    )
                )
                await uow.commit()
        return len(set(ids)), Usage()
