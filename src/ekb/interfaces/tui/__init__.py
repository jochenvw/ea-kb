import json
from pathlib import Path
from uuid import UUID

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Static,
    TabbedContent,
    TabPane,
)

from ekb.domain.models import Scope, SourceItem
from ekb.domain.resolution import Decision
from ekb.runtime import Runtime


class KnowledgeApp(App):
    TITLE = "Enterprise Knowledge Base"
    BINDINGS = [("q", "quit", "Quit"), ("r", "reload", "Refresh"), ("s", "scan", "Start / resume")]
    CSS = """
    #controls { height: 3; }
    #search { width: 1fr; }
    DataTable { height: 1fr; }
    #detail { height: 1fr; min-height: 12; }
    #status { height: auto; padding: 1; }
    #statistics { height: auto; padding: 1; }
    #benchmarks-pane Horizontal { height: 3; }
    """

    def __init__(
        self, runtime: Runtime, directory: Path | None = None, manifest: Path | None = None
    ):
        super().__init__()
        self.runtime = runtime
        self.directory = directory
        self.manifest = manifest

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Candidates are not approved enterprise policy.", id="status", markup=False)
        with Horizontal(id="controls"):
            yield Input(placeholder="Search concepts; press Enter", id="search")
            yield Button("Start / resume", id="scan")
            yield Button("Refresh", id="refresh")
        with TabbedContent():
            with TabPane("Sources", id="sources-pane"):
                yield DataTable(id="sources", cursor_type="row")
            with TabPane("Concepts", id="concepts-pane"):
                yield DataTable(id="concepts", cursor_type="row")
            with TabPane("Scans", id="scans-pane"):
                yield DataTable(id="scans", cursor_type="row")
            with TabPane("Tasks / failures", id="tasks-pane"):
                yield DataTable(id="tasks", cursor_type="row")
            with TabPane("Benchmarks", id="benchmarks-pane"):
                yield Input(value="mcsb-v2:NS-6", id="benchmark-reference")
                yield Input(value="mcsb-v1", placeholder="From edition", id="benchmark-from")
                yield Input(value="mcsb-v2", placeholder="To edition", id="benchmark-to")
                yield Checkbox("Compare whole editions", value=True, id="benchmark-whole")
                with Horizontal():
                    yield Button("Inspect lineage", id="benchmark-lineage")
                    yield Button("Compare (read-only)", id="benchmark-compare")
            with TabPane("Review", id="review-pane"):
                yield DataTable(id="issues", cursor_type="row")
                yield Input(placeholder="Finding / issue UUID", id="review-id")
                yield Input(placeholder="Actor (local trusted operator)", id="review-actor")
                yield Input(placeholder="Rationale", id="review-rationale")
                yield Input(placeholder="Previous decision UUID, if any", id="review-previous")
                yield Input(placeholder="Concept UUID", id="resolve-id")
                yield Input(placeholder="Version UUID for canonical proposal", id="review-version")
                yield Input(value="{}", placeholder="JSON applicability scope", id="review-scope")
                yield Checkbox("Explicit limited-scope prerelease", id="review-prerelease")
                with Horizontal():
                    yield Button("Accept", id="review-accept")
                    yield Button("Reject", id="review-reject")
                    yield Button("Reverse", id="review-reverse")
                    yield Button("Dismiss issue", id="review-dismiss")
                with Horizontal():
                    yield Button("Proposals / audit", id="review-history")
                    yield Button("Propose canonical", id="review-canonical")
                    yield Button("Resolve canonical", id="review-resolve")
        yield Static("", id="statistics", markup=False)
        with VerticalScroll(id="detail"):
            yield Static(
                "Select a concept for provenance and relationships.", id="evidence", markup=False
            )
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#sources", DataTable).add_columns(
            "Source", "Format", "Retrieval", "Publication", "Revision"
        )
        self.query_one("#concepts", DataTable).add_columns(
            "Kind", "Statement", "Modality", "Publication", "Evidence"
        )
        self.query_one("#scans", DataTable).add_columns(
            "Started", "Status", "Processed", "Skipped", "Failed"
        )
        self.query_one("#tasks", DataTable).add_columns("Task", "Status", "Attempts", "Error")
        self.query_one("#issues", DataTable).add_columns("Severity", "Type", "Status", "Observed")
        await self.action_reload()
        self.set_interval(3, self.action_reload)

    async def action_reload(self) -> None:
        async with self.runtime.database() as uow:
            sources = await uow.sources.list()
            scans = await uow.processing.scans()
            tasks = await uow.processing.tasks()
        concepts = await self.runtime.knowledge.search(self.query_one("#search", Input).value)
        stats = await self.runtime.knowledge.statistics()
        source_table = self.query_one("#sources", DataTable)
        source_table.clear()
        for source in sources:
            source_table.add_row(
                source.item.name,
                source.item.format,
                source.status,
                source.publication.publication_status.value,
                str(source.revision_id or ""),
                key=str(source.item.id),
            )
        concept_table = self.query_one("#concepts", DataTable)
        concept_table.clear()
        for concept in concepts:
            candidate = concept.versions[-1].candidate
            concept_table.add_row(
                candidate.semantic_type.value if candidate.semantic_type else "Unclassified",
                candidate.statement,
                candidate.modality.value,
                concept.versions[-1].publication.publication_status.value,
                str(len(concept.occurrences)),
                key=str(concept.id),
            )
        scan_table = self.query_one("#scans", DataTable)
        scan_table.clear()
        for scan in scans:
            scan_table.add_row(
                str(scan.started_at),
                scan.status,
                str(scan.processed),
                str(scan.skipped),
                str(scan.failed),
                key=str(scan.id),
            )
        task_table = self.query_one("#tasks", DataTable)
        task_table.clear()
        for task in tasks:
            task_table.add_row(
                str(task.id), task.status, str(task.attempts), task.error or "", key=str(task.id)
            )
        self.query_one("#statistics", Static).update(
            f"Tasks: {stats.task_count} | Tokens: {stats.input_tokens + stats.output_tokens} | "
            f"Latency: {stats.model_latency_ms:.1f} ms | "
            f"Configured cost: {stats.configured_cost} | "
            f"Validation failures: {stats.validation_failures}"
        )
        issues = await self.runtime.issues.list()
        issue_table = self.query_one("#issues", DataTable)
        issue_table.clear()
        for issue in issues:
            issue_table.add_row(
                issue.severity,
                issue.issue_type,
                issue.status,
                str(issue.observed),
                key=str(issue.id),
            )

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "benchmark-reference":
            self.action_benchmark()
            return
        await self.action_reload()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("review-"):
            self.action_review(event.button.id.removeprefix("review-"))
        elif event.button.id in {"benchmark-lineage", "benchmark-compare"}:
            self.action_benchmark(compare=event.button.id == "benchmark-compare")
        elif event.button.id == "scan":
            self.action_scan()
        else:
            await self.action_reload()

    @work(exclusive=True, group="review")
    async def action_review(self, action: str) -> None:
        try:
            actor = self.query_one("#review-actor", Input).value
            rationale = self.query_one("#review-rationale", Input).value
            scope = Scope.model_validate_json(self.query_one("#review-scope", Input).value)
            if action == "history":
                result = {
                    "findings": [
                        f.model_dump(mode="json")
                        for f in await self.runtime.reconciliation.findings()
                    ],
                    "decisions": [
                        d.model_dump(mode="json")
                        for d in await self.runtime.reconciliation.history()
                    ],
                }
                self.query_one("#evidence", Static).update(json.dumps(result, indent=2))
                return
            if action == "resolve":
                result_model = await self.runtime.resolver.resolve_concept(
                    UUID(self.query_one("#resolve-id", Input).value), scope
                )
            elif action == "canonical":
                if not actor:
                    raise ValueError("A local actor is required")
                result_model = await self.runtime.reconciliation.propose_canonical(
                    UUID(self.query_one("#resolve-id", Input).value),
                    UUID(self.query_one("#review-version", Input).value),
                    scope,
                    self.query_one("#review-prerelease", Checkbox).value,
                    actor,
                )
            elif action == "dismiss":
                result_model = await self.runtime.issues.review(
                    UUID(self.query_one("#review-id", Input).value), "dismissed", actor, rationale
                )
            else:
                previous = self.query_one("#review-previous", Input).value
                result_model = await self.runtime.reconciliation.decide(
                    Decision.model_validate(
                        {
                            "finding_id": self.query_one("#review-id", Input).value,
                            "outcome": {
                                "accept": "accepted",
                                "reject": "rejected",
                                "reverse": "reversed",
                            }[action],
                            "actor": actor,
                            "rationale": rationale,
                            "previous_id": previous or None,
                        }
                    )
                )
            self.query_one("#evidence", Static).update(result_model.model_dump_json(indent=2))
            await self.action_reload()
        except ValueError as error:
            self.query_one("#status", Static).update(str(error))

    @work(exclusive=True, group="benchmark")
    async def action_benchmark(self, compare: bool = False) -> None:
        reference = self.query_one("#benchmark-reference", Input).value
        try:
            if compare:
                whole = self.query_one("#benchmark-whole", Checkbox).value
                result = await self.runtime.comparisons.compare(
                    self.query_one("#benchmark-from", Input).value,
                    self.query_one("#benchmark-to", Input).value,
                    None if whole else reference.partition(":")[2],
                    read_only=True,
                )
            else:
                result = await self.runtime.comparisons.lineage(reference)
            self.query_one("#evidence", Static).update(result.model_dump_json(indent=2))
            self.query_one("#status", Static).update(
                "Read-only benchmark evidence; semantic transitions require review."
            )
        except ValueError as error:
            self.query_one("#status", Static).update(str(error))

    @work(exclusive=True, group="scan")
    async def action_scan(self) -> None:
        def progress(item: SourceItem, status: str) -> None:
            self.query_one("#status", Static).update(f"{item.name}: {status}")

        try:
            pipeline = self.runtime.pipeline()
            pipeline.progress = progress
            report = await pipeline.scan(self.runtime.connectors(self.directory, self.manifest))
            self.query_one("#status", Static).update(
                f"Scan {report.status}: {report.processed} processed, {report.skipped} skipped, "
                f"{report.failed} failed."
            )
            await self.action_reload()
        except Exception as error:
            self.query_one("#status", Static).update(f"Scan failed: {type(error).__name__}")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "issues":
            issue_id = UUID(str(event.row_key.value))
            self.query_one("#review-id", Input).value = str(issue_id)
            detail = await self.runtime.issues.get(issue_id)
            proposals = [
                f
                for fid in (detail.issue.finding_ids[:10] if detail else ())
                if (f := await self.runtime.reconciliation.get(fid))
            ]
            comparisons = [
                c.model_dump(mode="json")
                for cid in dict.fromkeys(cid for f in proposals for cid in f.concept_ids)
                if (c := await self.runtime.knowledge.get(cid))
            ]
            self.query_one("#evidence", Static).update(
                json.dumps(
                    {
                        "issue": detail.model_dump(mode="json") if detail else None,
                        "findings": [f.model_dump(mode="json") for f in proposals],
                        "competing_concepts": comparisons,
                    },
                    indent=2,
                )
            )
            return
        if event.data_table.id != "concepts":
            return
        concept_id = UUID(str(event.row_key.value))
        detail = await self.runtime.knowledge.get(concept_id)
        provenance = await self.runtime.knowledge.provenance(concept_id)
        value = {
            "concept": detail.model_dump(mode="json") if detail else None,
            "provenance": [p.model_dump(mode="json") for p in provenance],
        }
        self.query_one("#evidence", Static).update(json.dumps(value, indent=2))
