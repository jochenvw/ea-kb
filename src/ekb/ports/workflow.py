from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Protocol
from uuid import UUID

from ekb.domain.execution import (
    ArtifactReference,
    CheckOptions,
    Execution,
    ProcessorSpec,
    StageOptions,
)
from ekb.domain.issues import Issue, IssueDetail, IssueStatus
from ekb.domain.models import Model, ScanReport, Scope, SourceItem, Usage
from ekb.domain.reconciliation import SemanticCacheEntry
from ekb.domain.resolution import (
    Decision,
    Deviation,
    Finding,
    KnowledgeSnapshot,
)


class CheckContext(Model):
    run_id: UUID
    input_revision: str
    snapshot: KnowledgeSnapshot
    options: CheckOptions
    stage_configuration: StageOptions = StageOptions()
    check_version: str = "1"


class CheckResult(Model):
    findings: tuple[Finding, ...] = ()
    issues: tuple[Issue, ...] = ()
    candidates: int = 0
    model_calls: int = 0
    embedding_calls: int = 0
    usage: Usage = Usage()


class PipelineProgress(Protocol):
    def source(self, item: SourceItem, status: str) -> None: ...
    def phase(self, phase: str, report: ScanReport) -> None: ...
    def execution(self, execution: Execution) -> None: ...


class KnowledgeCheck(Protocol):
    spec: ProcessorSpec

    def validate_configuration(self, options: CheckOptions) -> None: ...

    async def evaluate(self, context: CheckContext) -> CheckResult: ...


class StageContext(Model):
    run_id: UUID
    execution_id: UUID
    owner: UUID
    inputs: tuple[ArtifactReference, ...] = ()
    configuration: StageOptions = StageOptions()


class PipelineStage(Protocol):
    spec: ProcessorSpec
    checks: tuple[KnowledgeCheck, ...]

    async def execute(self, context: StageContext) -> ArtifactReference: ...


class WorkflowRepository(Protocol):
    async def save_execution(self, execution: Execution) -> None: ...
    async def execution(self, execution_id: UUID) -> Execution | None: ...
    async def executions(
        self, parent_id: UUID | None = None, limit: int = 100
    ) -> list[Execution]: ...
    async def successful(
        self, processor_id: str, fingerprint: str | None = None
    ) -> Execution | None: ...
    async def semantic_cache(self, keys: list[str]) -> dict[str, SemanticCacheEntry]: ...
    async def save_semantic_cache(self, entries: dict[str, SemanticCacheEntry]) -> None: ...
    async def lease(self, name: str, owner: UUID, seconds: int = 120) -> bool: ...
    async def renew(self, name: str, owner: UUID, seconds: int = 120) -> bool: ...
    async def release(self, name: str, owner: UUID) -> None: ...


class ResolutionRepository(Protocol):
    async def snapshot(self) -> KnowledgeSnapshot: ...
    async def save_finding(self, finding: Finding) -> None: ...
    async def finding(self, finding_id: UUID) -> Finding | None: ...
    async def findings(
        self,
        limit: int = 100,
        *,
        check_id: str | None = None,
        after: UUID | None = None,
    ) -> list[Finding]: ...
    async def decisions(self, finding_id: UUID | None = None) -> list[Decision]: ...
    async def apply_decision(self, decision: Decision) -> Decision: ...
    async def deviations(self, as_of: date) -> tuple[Deviation, ...]: ...


class IssueRepository(Protocol):
    async def list(
        self,
        status: IssueStatus | None = None,
        issue_type: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        scope: Scope | None = None,
    ) -> list[Issue]: ...
    async def get(self, issue_id: UUID) -> IssueDetail | None: ...
    async def observe(self, issues: tuple[Issue, ...], check_id: str, run_id: UUID) -> int: ...
    async def review(
        self,
        issue_id: UUID,
        status: IssueStatus,
        actor: str,
        rationale: str,
        owner: str | None = None,
    ) -> IssueDetail: ...


type IndexOperation = Callable[[CheckContext], Awaitable[CheckResult]]
