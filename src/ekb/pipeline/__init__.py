from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Callable
from contextlib import nullcontext
from uuid import UUID, uuid4

from ekb.domain.models import (
    Extraction,
    InferenceResult,
    ProcessingTask,
    ScanReport,
    SourceItem,
    SourceRecord,
    SourceRevision,
    Usage,
    content_hash,
    fingerprint,
    now,
    parsing_fingerprint,
    stable_id,
    total_usage,
)
from ekb.domain.publication import assess_publication, publication_fingerprint
from ekb.domain.skills import validate_labeling, validate_scoped_extraction
from ekb.observability import log, tracer
from ekb.ports import (
    ArtifactStore,
    DocumentParser,
    IngestionSkills,
    ScanLeaseLostError,
    SourceConnector,
    SourceMissingError,
    UnitOfWorkFactory,
)


class ProcessingIncompleteError(RuntimeError):
    pass


class IngestionPipeline:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        artifacts: ArtifactStore,
        parser: DocumentParser,
        inference: IngestionSkills,
        *,
        concurrency: int = 4,
        parse_concurrency: int = 2,
        model_concurrency: int = 2,
        inference_config: str = "",
        progress: Callable[[SourceItem, str], None] | None = None,
        require_complete: bool = False,
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
            labeling=inference.label_fingerprint,
            extraction=inference.extraction_fingerprint,
            config=inference_config,
        )
        self.progress = progress or (lambda item, status: None)
        self.require_complete = require_complete

    def _processing_fingerprint(self, content: str, item: SourceItem) -> str:
        return fingerprint(
            content=content,
            parser=parsing_fingerprint(self.parser.version, item),
            inference=self.inference_fingerprint,
            publication=publication_fingerprint(
                item, parsing_fingerprint(self.parser.version, item)
            ),
        )

    async def scan(
        self,
        connectors: list[SourceConnector],
        *,
        dry_run: bool = False,
        staged: bool = True,
        phase: Callable[[str, ScanReport], None] | None = None,
    ) -> ScanReport:
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

        def notify(item: SourceItem, status: str) -> None:
            if status in {"known", "unknown", "downloaded", "reused", "metadata_only"}:
                increment(**{status: 1})
            self.progress(item, status)

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
            except ScanLeaseLostError:
                raise
            except Exception as error:
                failure("discovery", error)

        async def producer() -> None:
            async with asyncio.TaskGroup() as group:
                for connector in connectors:
                    group.create_task(discover(connector))
            for _ in range(self.concurrency):
                await queue.put(None)

        inventory_lock = asyncio.Lock()

        async def worker() -> None:
            while (work := await queue.get()) is not None:
                connector, item = work
                self.progress(item, "checking")
                try:
                    revision, content, ready = await self._prepare(connector, item, dry_run, notify)
                    if not ready:
                        increment(skipped=1)
                        self.progress(item, "skipped")
                    elif dry_run:
                        increment(processed=1)
                    elif inventory is not None:
                        assert revision is not None
                        line = json.dumps(
                            {
                                "item": item.model_dump(mode="json"),
                                "revision": revision.model_dump(mode="json"),
                            }
                        )
                        async with inventory_lock:
                            await asyncio.to_thread(inventory.write, line + "\n")
                    else:
                        assert revision is not None
                        outcome, concepts = await self._ingest(item, revision, content)
                        increment(**{outcome: 1}, concepts=concepts)
                        self.progress(item, outcome)
                except ScanLeaseLostError:
                    raise
                except Exception as error:
                    failure(str(item.id), error)
                    self.progress(item, "failed")
                await save_report()

        prepared: asyncio.Queue[tuple[SourceItem, SourceRevision] | None] = asyncio.Queue(
            maxsize=self.concurrency * 2
        )

        async def prepared_producer() -> None:
            assert inventory is not None
            await asyncio.to_thread(inventory.seek, 0)
            while line := await asyncio.to_thread(inventory.readline):
                entry = json.loads(line)
                await prepared.put(
                    (
                        SourceItem.model_validate(entry["item"]),
                        SourceRevision.model_validate(entry["revision"]),
                    )
                )
            for _ in range(self.concurrency):
                await prepared.put(None)

        async def prepared_worker() -> None:
            while (work := await prepared.get()) is not None:
                item, revision = work
                try:
                    outcome, concepts = await self._ingest(item, revision, None)
                    increment(**{outcome: 1}, concepts=concepts)
                    self.progress(item, outcome)
                except ScanLeaseLostError:
                    raise
                except Exception as error:
                    failure(str(item.id), error)
                    self.progress(item, "failed")
                await save_report()

        async with nullcontext() if dry_run else self.uow.scan_guard(report.id):
            await save_report()
            spool = (
                tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
                if staged
                else nullcontext(None)
            )
            with (
                spool as inventory,
                tracer.start_as_current_span("scan", attributes={"scan.id": str(report.id)}),
            ):
                try:
                    if phase:
                        phase("discovery", report)
                    async with asyncio.TaskGroup() as group:
                        group.create_task(producer())
                        for _ in range(self.concurrency):
                            group.create_task(worker())
                    if inventory is not None:
                        if phase:
                            phase("discovered", report)
                        async with asyncio.TaskGroup() as group:
                            group.create_task(prepared_producer())
                            for _ in range(self.concurrency):
                                group.create_task(prepared_worker())
                    report = report.model_copy(
                        update={"status": "completed" if report.failed == 0 else "partial"}
                    )
                except asyncio.CancelledError:
                    report = report.model_copy(update={"status": "cancelled"})
                    raise
                finally:
                    report = report.model_copy(update={"finished_at": now()})
                    await asyncio.shield(save_report())
                    if phase:
                        phase("completed", report)
        return report

    async def _prepare(
        self,
        connector: SourceConnector,
        item: SourceItem,
        dry_run: bool,
        notify: Callable[[SourceItem, str], None],
    ) -> tuple[SourceRevision | None, bytes | None, bool]:
        async with self.uow() as uow:
            previous = await uow.sources.get(item.id)
        notify(item, "known" if previous else "unknown")
        if item.metadata.get("metadata_only"):
            if not dry_run:
                async with self.uow() as uow:
                    await uow.sources.save(SourceRecord(item=item, status="metadata_only"))
                    await uow.commit()
            notify(item, "metadata_only")
            return None, None, False
        with tracer.start_as_current_span(
            "revision.comparison", attributes={"source.id": str(item.id)}
        ):
            async with self.uow() as uow:
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
                return revision, None, not (same_metadata and task and task.status == "done")
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
                notify(item, "reused")
                return revision, None, False

        content = None
        source = previous or SourceRecord(item=item)
        if not same_metadata:
            try:
                with tracer.start_as_current_span("document.download"):
                    fetched = await connector.fetch(item, previous)
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
            if fetched.not_modified:
                notify(item, "reused")
            elif item.location.startswith(("https://", "http://")):
                notify(item, "downloaded")
            else:
                notify(item, "read")
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
                # Check scan ownership before storage I/O without holding a transaction over it.
                async with self.uow() as uow:
                    await uow.commit()
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
        else:
            notify(item, "reused")
        assert revision is not None
        async with self.uow() as uow:
            await uow.sources.save(source)
            revision = await uow.sources.save_revision(revision)
            await uow.sources.save(source.model_copy(update={"revision_id": revision.id}))
            await uow.commit()
        return revision, content, True

    async def _ingest(
        self, item: SourceItem, revision: SourceRevision, content: bytes | None
    ) -> tuple[str, int]:
        process_fp = self._processing_fingerprint(revision.content_hash, item)
        task = ProcessingTask(
            id=stable_id(revision.id, process_fp), revision_id=revision.id, fingerprint=process_fp
        )
        owner = uuid4()
        async with self.uow() as uow:
            claimed = await uow.processing.claim(task, owner)
            await uow.commit()
        if not claimed:
            if self.require_complete:
                async with self.uow() as uow:
                    current = await uow.processing.task(revision.id, process_fp)
                if current is None or current.status != "done":
                    raise ProcessingIncompleteError("Processing task is not complete; retry")
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
            context = dict(
                source=item.id,
                text=segment.text,
                heading=segment.heading,
                control=segment.location.get("control_id"),
                part=segment.location.get("part_id"),
                structured=segment.location.get("structured_control"),
            )
            label_fp = fingerprint(skill=self.inference.label_fingerprint, **context)
            async with self.uow() as uow:
                cached_label = await uow.processing.cached(segment.id, label_fp)
            if cached_label is None:
                async with self.model_gate:
                    self.progress(item, f"labeling segment {segment.ordinal + 1}/{len(segments)}")
                    labeling = await self.inference.label(segment)
                validate_labeling(labeling.labeling, segment)
                cached_label = InferenceResult(
                    extraction=Extraction(),
                    labeling=labeling,
                    usage=labeling.usage,
                    model_id=labeling.model_id,
                    prompt_version=labeling.prompt_version,
                )
                async with self.uow() as uow:
                    await uow.processing.heartbeat(task_id, owner)
                    await uow.processing.cache(segment.id, label_fp, cached_label)
                    await uow.processing.accumulate_usage(task_id, owner, labeling.usage)
                    await uow.commit()
                usages.append(labeling.usage)
            labeling = cached_label.labeling
            if labeling is None:
                raise ValueError("Labeling checkpoint is missing its typed labeling result")
            validate_labeling(labeling.labeling, segment)
            cache_fp = fingerprint(
                inference=self.inference_fingerprint,
                labeling=labeling.labeling.model_dump(mode="json"),
                **context,
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
                        result = await self.inference.extract_scoped(segment, labeling.labeling)
                        result = result.model_copy(update={"labeling": labeling})
                with tracer.start_as_current_span("schema.validation"):
                    validate_scoped_extraction(result.extraction, segment, labeling.labeling)
                usages.append(result.usage)
                called_model = True
            else:
                log.info("segment_replayed", segment_id=str(segment.id))
                called_model = False
            validate_scoped_extraction(result.extraction, segment, labeling.labeling)
            async with self.uow() as uow:
                await uow.processing.heartbeat(task_id, owner)
                ids = await uow.knowledge.persist(segment, result)
                await uow.processing.cache(segment.id, cache_fp, result)
                if called_model:
                    await uow.processing.accumulate_usage(task_id, owner, result.usage)
                await uow.commit()
            count += len(set(ids))
        return count, total_usage(usages)
