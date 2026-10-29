from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

import typer
from sqlalchemy.engine import URL, make_url

from ekb.config import Settings
from ekb.domain.benchmarks import BenchmarkSyncReport
from ekb.domain.execution import Execution
from ekb.domain.models import ScanReport, SourceItem, WorkspaceCounts

BANNER = r"""
 _____ _  __ ____
| ____| |/ /| __ )
|  _| | ' / |  _ \
| |___| . \ | |_) |
|_____|_|\_\|____/
Enterprise Architecture Knowledge
SOURCES ==> KNOWLEDGE ==> ISSUES
"""


def safe(value: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", value)


def locations(settings: Settings, report: Path) -> dict[str, str]:
    url = make_url(settings.database_url.get_secret_value())
    return {
        "workspace": "http://127.0.0.1:8000",
        "issues": "http://127.0.0.1:8000/review",
        "pipeline": "http://127.0.0.1:8000/pipeline",
        "artifacts": (settings.data_dir / "artifacts").resolve().as_uri(),
        "benchmark_cache": (settings.data_dir / "test-corpus/public/microsoft_security_benchmark")
        .resolve()
        .as_uri(),
        "database": URL.create(
            "postgresql", host=url.host, port=url.port or 5432, database=url.database
        ).render_as_string(),
        "report": report.resolve().as_uri(),
    }


class ScanConsole:
    def __init__(self, *, quiet: bool = False):
        self.quiet = quiet
        self.report: ScanReport | None = None
        self.run_id: UUID | None = None

    def line(self, value: str = "") -> None:
        if not self.quiet:
            typer.echo(value)

    def start(self, settings: Settings, manifest: Path | None, directory: Path | None) -> None:
        self.line(BANNER)
        self.line(f"Profile: {safe(str(settings.config_file.resolve()))}")
        if manifest:
            from ekb.adapters.sources import read_manifest

            specifications = read_manifest(manifest)
            self.line(f"Sources YAML: {safe(str(manifest.resolve()))}")
            self.line(
                f"  {sum(s.enabled for s in specifications)} enabled definitions; "
                "benchmark seeds expand into individual files."
            )
            for source in specifications:
                status = (
                    "disabled"
                    if not source.enabled
                    else "metadata only"
                    if source.metadata_only
                    else source.retrieval
                )
                self.line(f"  [{status}] {safe(source.name)}")
        if directory:
            self.line(f"Source directory: {safe(str(directory.resolve()))}")
        self.line(
            f"Parallelism: {settings.scan_concurrency} discovery/download/file workers; "
            f"{settings.parse_concurrency} parsers; {settings.model_concurrency} model calls."
        )
        self.line(
            f"Extraction: {settings.inference} / "
            f"{safe(settings.model_id or 'deterministic rules')}; "
            f"reconciliation: {safe(settings.reconciliation_model_id or 'rules only')}; "
            f"embeddings: {safe(settings.embedding_model_id or 'disabled')}."
        )
        if settings.inference == "rules":
            self.line("No extraction LLM is enabled. Rules are not live-model qualification.")
        elif settings.model_input_price is None or settings.model_output_price is None:
            self.line("Paid model calls are enabled; unconfigured pricing means cost is unknown.")
        self.line()

    def source(self, item: SourceItem, status: str) -> None:
        if status in {
            "known",
            "unknown",
            "cache_known",
            "cache_new",
            "downloaded",
            "reused",
            "metadata_only",
            "processed",
            "failed",
        }:
            self.line(f"  [{status:13}] {safe(item.name)}  ({item.id})")

    def phase(self, phase: str, report: ScanReport) -> None:
        self.report = report
        if phase == "discovery":
            self.line("[1/3] Discover, compare revisions and acquire originals")
        elif phase == "discovered":
            self.line(
                f"Discovery summary: found={report.discovered}, known={report.known}, "
                f"not-known={report.unknown}, downloaded={report.downloaded}, "
                f"reused={report.reused}, metadata-only={report.metadata_only}, "
                f"missing={report.missing}, failed={report.failed}."
            )
            self.line("\n[2/3] Open files, extract candidates and persist exact evidence")
        elif phase == "completed":
            self.line(
                f"Extraction summary: processed={report.processed}, skipped={report.skipped}, "
                f"candidate results emitted={report.concepts}, failed={report.failed}."
            )

    def synchronized(self, report: BenchmarkSyncReport) -> None:
        self.line(
            f"Benchmark acquisition: found={report.discovered}, downloaded={report.downloaded}, "
            f"normalized-cache reused={report.skipped}, failed={report.failed}."
        )

    def execution(self, execution: Execution) -> None:
        if execution.kind == "pipeline":
            self.run_id = execution.id
        if execution.kind != "stage":
            return
        if execution.status == "running":
            if execution.processor_id == "indexing":
                self.line(
                    "[1/3] Source acquisition (including configured benchmark synchronization)"
                )
            elif execution.processor_id == "enrichment":
                self.line("\n[3/3] Reconcile knowledge, then identify persistent issues")
            elif execution.processor_id == "issues":
                self.line("  Running issue checks against reconciled evidence...")
        else:
            stats = execution.statistics
            self.line(
                f"{execution.processor_id.capitalize()} summary: {execution.status}; "
                f"new concepts={stats.get('new_concepts', 0)}, "
                f"new proposals={stats.get('new_proposals', 0)}, "
                f"new issues={stats.get('new_issues', 0)}, "
                f"checks done/reused={stats.get('checks_done', 0)}/{stats.get('checks_reused', 0)}."
            )

    def finish(
        self,
        status: str,
        before: WorkspaceCounts,
        after: WorkspaceCounts,
        links: dict[str, str],
    ) -> None:
        self.line(f"\nOVERALL SUMMARY: {status.upper()}")
        for field in (
            "sources",
            "revisions",
            "segments",
            "concepts",
            "versions",
            "occurrences",
            "proposals",
            "issues",
        ):
            total = getattr(after, field)
            self.line(
                f"  {field:12} {total:>8} total  "
                f"{total - getattr(before, field):+8} workspace change"
            )
        self.line(f"  Open issues: {after.open_issues}. Proposals are not approvals.")
        self.line("  UI links require the web server: uv run ekb web")
        for name, value in links.items():
            self.line(f"  {name:16} {safe(value)}")
