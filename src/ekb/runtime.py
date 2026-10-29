from __future__ import annotations

from pathlib import Path
from typing import Self

from ekb.adapters.artifacts import AzureBlobArtifactStore, FilesystemArtifactStore
from ekb.adapters.inference import AgentFrameworkInference, OpenAIInference, RulesInference
from ekb.adapters.parsers import StructuredParser
from ekb.adapters.persistence.postgres import Postgres
from ekb.adapters.sources import (
    FilesystemConnector,
    HTTPConnector,
    manifest_connector,
    read_manifest,
)
from ekb.adapters.sources.benchmarks import MicrosoftLearnBenchmarkConnector
from ekb.config import Settings
from ekb.domain.models import fingerprint
from ekb.observability import configure, flush
from ekb.pipeline import IngestionPipeline
from ekb.ports import ArtifactStore, InferencePort, SourceConnector
from ekb.services.knowledge import KnowledgeService


class Runtime:
    """Explicit composition root. All asynchronous resources share one event loop."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        configure(self.settings)
        self.database = Postgres(self.settings.database_url.get_secret_value())
        self.artifacts: ArtifactStore
        if self.settings.artifact_backend == "azure":
            if not self.settings.blob_account_url:
                raise ValueError("EKB_BLOB_ACCOUNT_URL is required for Azure artifacts")
            self.artifacts = AzureBlobArtifactStore(
                self.settings.blob_account_url, self.settings.blob_container
            )
        else:
            self.artifacts = FilesystemArtifactStore(
                self.settings.data_dir / "artifacts",
                document_directory=self.settings.data_dir
                / "test-corpus"
                / "public"
                / "microsoft_security_benchmark",
            )
        self.inference: InferencePort
        match self.settings.inference:
            case "openai":
                self.inference = OpenAIInference(self.settings)
            case "agent-framework":
                self.inference = AgentFrameworkInference(self.settings)
            case _:
                self.inference = RulesInference()
        self.parser = StructuredParser(
            ocr=self.settings.ocr, max_chars=self.settings.segment_max_chars
        )
        self.knowledge = KnowledgeService(self.database)
        self._connectors: list[SourceConnector] = []
        self._connector_cache: dict[tuple[str, str], list[SourceConnector]] = {}
        self._benchmarks: MicrosoftLearnBenchmarkConnector | None = None

    def benchmarks(self) -> MicrosoftLearnBenchmarkConnector:
        if self._benchmarks is None:
            self._benchmarks = MicrosoftLearnBenchmarkConnector(
                self.artifacts,
                self.settings.max_document_bytes,
                concurrency=min(self.settings.scan_concurrency, 16),
            )
            self._connectors.append(self._benchmarks)
        return self._benchmarks

    def pipeline(self) -> IngestionPipeline:
        return IngestionPipeline(
            self.database,
            self.artifacts,
            self.parser,
            self.inference,
            concurrency=self.settings.scan_concurrency,
            parse_concurrency=self.settings.parse_concurrency,
            model_concurrency=self.settings.model_concurrency,
            inference_config=fingerprint(
                endpoint=self.settings.model_endpoint,
                structured=self.settings.model_structured_output,
                context=self.settings.model_context_tokens,
                output_tokens=self.settings.model_max_output_tokens,
            ),
        )

    def connectors(
        self, directory: Path | None = None, manifest: Path | None = None
    ) -> list[SourceConnector]:
        connectors: list[SourceConnector] = []
        directory = directory or self.settings.source_directory
        key = (
            str(directory.resolve()) if directory else "",
            str(manifest.resolve()) if manifest else "",
        )
        if key in self._connector_cache:
            cached = self._connector_cache[key]
            if manifest:
                manifest_sources = read_manifest(manifest)
                for connector in cached:
                    if isinstance(connector, HTTPConnector) and not isinstance(
                        connector, MicrosoftLearnBenchmarkConnector
                    ):
                        connector.sources = [
                            source
                            for source in manifest_sources
                            if source.retrieval != "microsoft-security-benchmark"
                        ]
                benchmark_enabled = any(
                    source.enabled and source.retrieval == "microsoft-security-benchmark"
                    for source in manifest_sources
                )
                if benchmark_enabled and not any(
                    isinstance(connector, MicrosoftLearnBenchmarkConnector) for connector in cached
                ):
                    cached.append(self.benchmarks())
                elif not benchmark_enabled:
                    cached[:] = [
                        connector
                        for connector in cached
                        if not isinstance(connector, MicrosoftLearnBenchmarkConnector)
                    ]
            return cached
        if directory is not None:
            connectors.append(FilesystemConnector(directory, self.settings.max_document_bytes))
        if manifest is not None:
            connectors.append(manifest_connector(manifest, self.settings.max_document_bytes))
            if any(
                source.enabled and source.retrieval == "microsoft-security-benchmark"
                for source in read_manifest(manifest)
            ):
                connectors.append(self.benchmarks())
        if not connectors:
            connectors.append(self.benchmarks())
        self._connectors.extend(
            connector for connector in connectors if connector not in self._connectors
        )
        self._connector_cache[key] = connectors
        return connectors

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        for connector in self._connectors:
            if isinstance(connector, HTTPConnector):
                await connector.close()
        if isinstance(self.inference, OpenAIInference):
            await self.inference.close()
        if isinstance(self.artifacts, AzureBlobArtifactStore):
            await self.artifacts.close()
        await self.database.close()
        flush()
