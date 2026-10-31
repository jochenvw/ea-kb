from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Protocol, Self, runtime_checkable
from uuid import UUID

from ekb.domain.inspection import (
    BacklogPage,
    BacklogStatus,
    KnowledgeGraph,
    SourceInspection,
    TableInfo,
    TablePage,
)
from ekb.domain.models import (
    Assertion,
    AuthorityEvent,
    Capabilities,
    ConceptDetail,
    ConceptVersion,
    ContentLabeling,
    FetchResult,
    InferenceResult,
    KnowledgeLevel,
    LabelingResult,
    ProcessingTask,
    Provenance,
    PublicationMetadata,
    ScanReport,
    Segment,
    SemanticType,
    SourceItem,
    SourceRecord,
    SourceRevision,
    Usage,
    WorkspaceCounts,
)
from ekb.domain.reconciliation import EmbeddingResult, SemanticAssertion, Verification
from ekb.ports.workflow import IssueRepository, ResolutionRepository, WorkflowRepository


class SourceMissingError(FileNotFoundError):
    """The source is absent, not merely inaccessible."""


class ScanLeaseLostError(RuntimeError):
    """A scan no longer owns its durable write guard."""


class SourceConnector(Protocol):
    name: str

    def discover(self) -> AsyncIterator[SourceItem]: ...
    async def fetch(self, item: SourceItem, previous: SourceRecord | None) -> FetchResult: ...


class ArtifactStore(Protocol):
    async def put(self, content: bytes) -> str: ...
    async def get(self, key: str) -> bytes: ...


class DocumentParser(Protocol):
    version: str

    async def parse(
        self, item: SourceItem, revision: SourceRevision, content: bytes
    ) -> list[Segment]: ...


class InferencePort(Protocol):
    capabilities: Capabilities
    prompt_version: str

    async def extract(self, segment: Segment) -> InferenceResult: ...


class IngestionSkills(InferencePort, Protocol):
    label_fingerprint: str
    extraction_fingerprint: str

    async def label(self, segment: Segment) -> LabelingResult: ...
    async def extract_scoped(
        self, segment: Segment, labeling: ContentLabeling
    ) -> InferenceResult: ...


@runtime_checkable
class StrategyReconciliationPort(IngestionSkills, Protocol):
    strategy_reconciliation_fingerprint: str

    async def reconcile_strategy(self, segment: Segment) -> InferenceResult: ...


class ModelRequestLimiter(Protocol):
    async def reserve(self, deployment: str, requests: int) -> None: ...


@runtime_checkable
class RequestControlledSkills(Protocol):
    def limit_model_requests(
        self, limiter: ModelRequestLimiter
    ) -> AbstractContextManager[None]: ...


class AssertionWriter(Protocol):
    fingerprint: str

    async def rewrite(self, version: ConceptVersion, evidence: Provenance) -> Assertion: ...


class SemanticVerifier(Protocol):
    """Return a grounded judgment with an adapter-defined method and explicit failure status."""

    fingerprint: str

    async def verify(self, old: SemanticAssertion, new: SemanticAssertion) -> Verification: ...


class EmbeddingPort(Protocol):
    """Report request usage completeness independently of price when known."""

    model_id: str
    fingerprint: str

    async def embed(self, texts: list[str]) -> EmbeddingResult: ...


class SourceRepository(Protocol):
    async def get(self, source_id: UUID) -> SourceRecord | None: ...
    async def list(self) -> list[SourceRecord]: ...
    async def save(self, source: SourceRecord) -> None: ...
    async def revision(self, revision_id: UUID) -> SourceRevision | None: ...
    async def save_revision(self, revision: SourceRevision) -> SourceRevision: ...
    async def save_publication(
        self, revision_id: UUID, publication: PublicationMetadata, fingerprint: str
    ) -> None: ...
    async def save_segments(
        self, revision_id: UUID, segments: list[Segment], parser_fingerprint: str
    ) -> None: ...
    async def segments(
        self, revision_id: UUID, parser_fingerprint: str
    ) -> list[Segment] | None: ...


class KnowledgeRepository(Protocol):
    async def counts(self) -> WorkspaceCounts: ...
    async def search(
        self,
        query: str = "",
        limit: int = 50,
        *,
        after: UUID | None = None,
        source_id: UUID | None = None,
        level: KnowledgeLevel | None = None,
        semantic_types: tuple[SemanticType, ...] | None = None,
    ) -> list[ConceptDetail]: ...
    async def save_assertion(self, version_id: UUID, assertion: Assertion) -> None: ...
    async def get(self, concept_id: UUID) -> ConceptDetail | None: ...
    async def persist(self, segment: Segment, result: InferenceResult) -> list[UUID]: ...
    async def provenance(self, concept_id: UUID) -> list[Provenance]: ...
    async def similar(self, concept_id: UUID, limit: int = 10) -> list[ConceptDetail]: ...
    async def record_authority(self, event: AuthorityEvent) -> None: ...
    async def add_version(self, version: ConceptVersion) -> None: ...


class LineageRepository(Protocol):
    async def trace_origins(self, concept_id: UUID, max_depth: int = 20) -> list[UUID]: ...
    async def find_dependents(self, concept_id: UUID, max_depth: int = 20) -> list[UUID]: ...


class ProcessingRepository(Protocol):
    async def task(self, revision_id: UUID, fingerprint: str) -> ProcessingTask | None: ...
    async def claim(self, task: ProcessingTask, owner: UUID) -> bool: ...
    async def heartbeat(self, task_id: UUID, owner: UUID) -> None: ...
    async def accumulate_usage(self, task_id: UUID, owner: UUID, usage: Usage) -> None: ...
    async def finish(self, task: ProcessingTask, owner: UUID) -> None: ...
    async def save_scan(self, report: ScanReport) -> None: ...
    async def scans(self, limit: int = 25) -> list[ScanReport]: ...
    async def tasks(self, limit: int = 100, after: UUID | None = None) -> list[ProcessingTask]: ...
    async def cached(self, segment_id: UUID, fingerprint: str) -> InferenceResult | None: ...
    async def cache(self, segment_id: UUID, fingerprint: str, result: InferenceResult) -> None: ...


class InspectionRepository(Protocol):
    async def tables(self) -> list[TableInfo]: ...
    async def rows(
        self, name: str, limit: int = 50, *, after: str | None = None, record: str | None = None
    ) -> TablePage: ...
    async def source(
        self, source_id: UUID, limit: int = 50, *, after: UUID | None = None
    ) -> SourceInspection | None: ...
    async def graph(self, concept_id: UUID) -> KnowledgeGraph | None: ...
    async def backlog(
        self,
        limit: int = 50,
        *,
        after: UUID | None = None,
        status: BacklogStatus = "all",
        check_id: str | None = None,
    ) -> BacklogPage: ...


class UnitOfWork(Protocol):
    @property
    def inspection(self) -> InspectionRepository: ...
    @property
    def workflow(self) -> WorkflowRepository: ...
    @property
    def resolution(self) -> ResolutionRepository: ...
    @property
    def issues(self) -> IssueRepository: ...
    @property
    def sources(self) -> SourceRepository: ...
    @property
    def knowledge(self) -> KnowledgeRepository: ...
    @property
    def lineage(self) -> LineageRepository: ...
    @property
    def processing(self) -> ProcessingRepository: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *args: object) -> None: ...
    async def commit(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
    def scan_guard(self, scan_id: UUID) -> AbstractAsyncContextManager[None]: ...
