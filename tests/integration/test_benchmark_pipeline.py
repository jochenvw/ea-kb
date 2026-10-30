from __future__ import annotations

import json
from collections import Counter
from uuid import uuid4

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
from ekb.domain.models import (
    PublicationStatus,
    SemanticType,
    SourceItem,
    SourceRecord,
    SourceRevision,
    content_hash,
    fingerprint,
)
from ekb.domain.reconciliation import EmbeddingResult, structural_verification
from ekb.pipeline import IngestionPipeline
from ekb.services.reconciliation import BenchmarkService

pytestmark = pytest.mark.integration


async def test_native_mapping_resolution_requires_exact_catalog_revision_and_published_label(
    database,
    tmp_path,
):
    parser, inference = StructuredParser(), RulesInference()
    expected = {}
    for revision in ("4", "5", None):
        source_id = uuid4()
        item = SourceItem(
            id=source_id,
            connector="synthetic-native-test",
            location=f"urn:synthetic:{source_id}",
            name="Synthetic native catalog",
            format="oscal",
            metadata={"framework_namespace": "nist-sp-800-53", "framework_revision": revision},
        )
        content = json.dumps(
            {
                "catalog": {
                    "controls": [
                        {
                            "id": "sc-7.5",
                            "title": "Synthetic enhancement",
                            "props": [{"name": "label", "value": "SC-7(5)"}],
                            "parts": [
                                {
                                    "id": "statement",
                                    "prose": f"Revision {revision} must deny traffic by default.",
                                }
                            ],
                        }
                    ]
                }
            }
        ).encode()
        source_revision = SourceRevision(
            id=uuid4(), source_id=source_id, content_hash=content_hash(content), artifact_key="test"
        )
        segments = await parser.parse(item, source_revision, content)
        async with database() as uow:
            await uow.sources.save(SourceRecord(item=item, revision_id=source_revision.id))
            await uow.sources.save_revision(source_revision)
            await uow.sources.save_segments(source_revision.id, segments, parser.version)
            expected[revision] = await uow.knowledge.persist(
                segments[0], await inference.extract(segments[0])
            )
            await uow.commit()
    async with database() as uow:
        targets = await uow.benchmarks.native_controls("nist-sp-800-53", "5", "SC-7(5)")
        assert len(targets) == 1
        assert targets[0].control_id == "sc-7.5"
        assert set(targets[0].concept_ids) == set(expected["5"])
        assert await uow.benchmarks.native_controls("nist-sp-800-53", "5", "SC-7") == []
        assert await uow.benchmarks.native_controls("nist-sp-800-53", "3", "SC-7(5)") == []
        assert await uow.benchmarks.native_controls("nist-csf", "5", "SC-7(5)") == []
    body = (
        "# Synthetic benchmark\n\n## NS-6: Protect applications\n\n"
        "### Security principle\n\nProtect applications with a firewall.\n\n"
        "### NS-6.1: Configure a firewall\n\nChoose Application Gateway or Front Door.\n\n"
        "### Control mapping\n\n- **NIST SP800-53 Rev.5:** SC-7(5), SC-7\n"
    )
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    ) as client:
        connector = MicrosoftLearnBenchmarkConnector(artifacts, client=client)
        await connector._sync_asset(
            _asset("mcsb-v2", LEARN + "mcsb-v2-network-security", "domain", domain="NS"), Counter()
        )
        report = await IngestionPipeline(database, artifacts, parser, inference).scan([connector])
        assert report.failed == 0
    async with database() as uow:
        record = (await uow.benchmarks.documents("mcsb-v2", "NS-6"))[0]
        parent, child = record.concept_ids["NS-6"], record.concept_ids["NS-6.1"]
        graph = await uow.inspection.graph(parent)
        assert graph
        references = [node for node in graph.nodes if node.kind == "framework_control"]
        assert len(references) == 1 and "SC-7(5)" in references[0].label
        mappings = [edge for edge in graph.edges if edge.kind == "mapsTo"]
        assert len(mappings) == 1 and mappings[0].status == "published"
        assert mappings[0].evidence == record.document.controls[0].mappings[0].evidence
        assert next(node for node in graph.nodes if node.id == parent).level == "implementation"
        native = await uow.inspection.graph(expected["5"][0])
        assert native and any(
            edge.source == parent and edge.kind == "mapsTo" for edge in native.edges
        )
        assert any(edge.kind == "part_of_control" for edge in native.edges)
        legacy = await uow.inspection.graph(expected["4"][0])
        assert legacy and not any(edge.kind == "mapsTo" for edge in legacy.edges)
        detail = await uow.inspection.graph(child)
        assert detail and any(
            edge.source == child and edge.target == parent and edge.kind == "implementation_detail"
            for edge in detail.edges
        )
        assert not any(edge.source == child and edge.kind == "mapsTo" for edge in detail.edges)
        source = await uow.sources.get(targets[0].source_id)
        assert source
        replacement = SourceRevision(
            id=uuid4(),
            source_id=source.item.id,
            content_hash=content_hash(b"new catalog"),
            artifact_key="test",
        )
        await uow.sources.save_revision(replacement)
        await uow.sources.save(source.model_copy(update={"revision_id": replacement.id}))
        await uow.commit()
    async with database() as uow:
        stale = await uow.inspection.graph(parent)
        assert stale and not any(edge.kind == "mapsTo" for edge in stale.edges)


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
            assert detail.versions[0].candidate.semantic_type == SemanticType.PLATFORM_REQUIREMENT
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

        class FakeVerifier:
            fingerprint = "synthetic-model-contract"
            calls = 0

            async def verify(self, old, new):
                self.calls += 1
                return structural_verification(old, new).model_copy(
                    update={"method": "small_model", "model_calls": 1}
                )

        class FakeEmbeddings:
            model_id = fingerprint = "synthetic-embedding-contract"
            calls = 0

            async def embed(self, texts):
                self.calls += 1
                return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts), calls=1)

        verifier, embeddings = FakeVerifier(), FakeEmbeddings()
        service = BenchmarkService(database, verifier, embeddings)
        preview = await service.compare("mcsb-v1", "mcsb-v2", "NS-6", read_only=True)
        assert preview.model_calls == preview.embedding_calls == 0
        assert verifier.calls == embeddings.calls == 0
        async with database() as uow:
            assert await uow.benchmarks.comparison(preview.id) is None
        comparison = await service.compare("mcsb-v1", "mcsb-v2", "NS-6")
        assert verifier.calls == embeddings.calls == 1
        assert comparison.changes[0].review_status == "proposed"
        assert comparison.changes[0].proposed_semantic_version is None
        assert comparison.changes[0].new is not None
        assert comparison.changes[0].new.policies == ()
        cached = await service.compare("mcsb-v1", "mcsb-v2", "NS-6")
        assert cached.cache_hit and cached.model_calls == cached.embedding_calls == 0
        assert verifier.calls == embeddings.calls == 1
        change = comparison.changes[0]
        assert change.new is not None
        binding = change.new.mappings[0]
        forged_binding = binding.model_copy(
            update={"reference": binding.reference.model_copy(update={"control_id": "FAKE-999"})}
        )
        forged_change = change.model_copy(
            update={"new": change.new.model_copy(update={"mappings": (forged_binding,)})}
        )
        async with database() as uow:
            with pytest.raises(ValueError, match="exact published control reference"):
                await uow.benchmarks.save_comparison(
                    comparison.model_copy(update={"id": uuid4(), "changes": (forged_change,)})
                )
        chain = await service.lineage("mcsb-v2:NS-6")
        assert len(chain.semantic_proposals) == 1

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
        replay_comparison = await service.compare("mcsb-v1", "mcsb-v2", "NS-6")
        assert replay_comparison.id != comparison.id
        assert verifier.calls == embeddings.calls == 1
        verification = replay_comparison.changes[0].verification
        assert verification is not None and verification.cache_hit
