from __future__ import annotations

from collections import Counter

import httpx
import pytest
from sqlalchemy import func, select

from ekb.adapters.artifacts import FilesystemArtifactStore
from ekb.adapters.inference import RulesInference
from ekb.adapters.parsers import StructuredParser
from ekb.adapters.persistence.postgres.models import (
    BenchmarkDocumentRow,
    ConceptRow,
    OccurrenceRow,
    RevisionRow,
    SegmentRow,
)
from ekb.adapters.sources.benchmarks import LEARN, MicrosoftLearnBenchmarkConnector, _asset
from ekb.domain.models import ConceptKind, PublicationStatus, fingerprint
from ekb.pipeline import IngestionPipeline

pytestmark = pytest.mark.integration


async def test_cached_benchmarks_persist_exact_records_without_parsing_or_model_calls(
    database,
    tmp_path,
    monkeypatch,
):
    bodies = {
        "mcsb-network-security": (
            "# Synthetic network benchmark\n\n"
            "## NS-6: Deploy a firewall\n\n"
            "**Security principle**: Protect applications with a firewall.\n\n"
            "**Azure guidance**: Configure inspection.\n"
        ),
        "mcsb-v2-network-security": (
            "# Synthetic network benchmark (preview)\n\n"
            "## NS-6: Deploy a firewall\n\n"
            "### Security principle\n\nProtect applications and inspect HTTP traffic.\n\n"
            "### NS-6.1: Configure a firewall\n\nChoose Application Gateway or Front Door.\n\n"
            "### Control mapping\n\n- **NIST SP800-53 Rev.5:** SC-7, SC-7(5)\n"
        ),
    }

    def handle(request):
        return httpx.Response(200, text=bodies[request.url.path.rsplit("/", 1)[-1]])

    artifacts = FilesystemArtifactStore(
        tmp_path / "artifacts",
        document_directory=tmp_path / "benchmark",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        connector = MicrosoftLearnBenchmarkConnector(artifacts, client=client)
        for edition, slug in (
            ("mcsb-v1", "mcsb-network-security"),
            ("mcsb-v2", "mcsb-v2-network-security"),
        ):
            await connector._sync_asset(
                _asset(edition, LEARN + slug, "domain", domain="NS"), Counter()
            )

        async def forbidden(*args, **kwargs):
            raise AssertionError("Structured cached benchmarks must not invoke a parser or model")

        inference, parser = RulesInference(), StructuredParser()
        monkeypatch.setattr(inference, "extract", forbidden)
        monkeypatch.setattr(parser, "parse", forbidden)
        pipeline = IngestionPipeline(database, artifacts, parser, inference)
        preview = await pipeline.scan([connector], dry_run=True)
        assert preview.processed == 2 and preview.failed == 0
        async with database() as uow:
            assert await uow.sources.list() == []
            assert await uow.benchmarks.documents() == []
        first = await pipeline.scan([connector])
        assert first.failed == 0 and first.processed == 2
        async with database() as uow:
            records = await uow.benchmarks.documents("mcsb-v2", "NS-6")
            assert len(records) == 1
            record = records[0]
            assert set(record.concept_ids) == {"NS-6", "NS-6.1"}
            assert record.document.controls[0].mappings[1].control_id == "SC-7(5)"
            assert record.document.publication.publication_status == PublicationStatus.PREVIEW
            control = record.document.controls[0]
            reference = control.mappings[0]
            forged_reference = reference.model_copy(
                update={
                    "evidence": reference.evidence.model_copy(
                        update={
                            "excerpt": "X" * len(reference.evidence.excerpt),
                        }
                    ),
                }
            )
            forged = record.model_copy(
                update={
                    "document": record.document.model_copy(
                        update={
                            "controls": (
                                control.model_copy(
                                    update={
                                        "mappings": (forged_reference, *control.mappings[1:]),
                                    }
                                ),
                            ),
                        }
                    ),
                }
            )
            with pytest.raises(ValueError, match="exact stored original"):
                await uow.benchmarks.save(forged)
            detail = await uow.knowledge.get(record.concept_ids["NS-6"])
            assert detail and detail.versions[0].semantic_version is None
            assert detail.versions[0].candidate.kind == ConceptKind.REQUIREMENT
            assert all(
                state.canonical_status == "not_canonical" for state in detail.authority_states
            )
            for provenance in await uow.knowledge.provenance(detail.id):
                evidence = provenance.occurrence
                assert provenance.segment.text[evidence.start : evidence.end] == evidence.excerpt
            counts = {
                table.__tablename__: await uow.session.scalar(
                    select(func.count()).select_from(table)
                )
                for table in (
                    BenchmarkDocumentRow,
                    ConceptRow,
                    RevisionRow,
                    SegmentRow,
                    OccurrenceRow,
                )
            }
        second = await pipeline.scan([connector])
        assert second.skipped == 2 and second.failed == 0
        async with database() as uow:
            assert counts == {
                table.__tablename__: await uow.session.scalar(
                    select(func.count()).select_from(table)
                )
                for table in (
                    BenchmarkDocumentRow,
                    ConceptRow,
                    RevisionRow,
                    SegmentRow,
                    OccurrenceRow,
                )
            }
        pipeline.inference_fingerprint = fingerprint(model="another-configured-model")
        assert (await pipeline.scan([connector])).skipped == 2
        import ekb.adapters.parsers.benchmarks as parser_module
        import ekb.adapters.sources.benchmarks as connector_module

        version = fingerprint(parser="synthetic-benchmark-parser-replay")
        monkeypatch.setattr(parser_module, "BENCHMARK_PARSER_VERSION", version)
        monkeypatch.setattr(connector_module, "BENCHMARK_PARSER_VERSION", version)
        for edition, slug in (
            ("mcsb-v1", "mcsb-network-security"),
            ("mcsb-v2", "mcsb-v2-network-security"),
        ):
            await connector._sync_asset(
                _asset(edition, LEARN + slug, "domain", domain="NS"),
                Counter(),
            )
        replay = await pipeline.scan([connector])
        assert replay.processed == 2 and replay.failed == 0
        async with database() as uow:
            assert await uow.session.scalar(select(func.count()).select_from(RevisionRow)) == 2
            assert await uow.session.scalar(select(func.count()).select_from(ConceptRow)) == 3
            assert len(await uow.benchmarks.documents("mcsb-v2", "NS-6", history=True)) == 2
            assert len(await uow.benchmarks.documents("mcsb-v2", "NS-6")) == 1
