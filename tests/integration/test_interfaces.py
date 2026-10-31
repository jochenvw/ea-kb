import asyncio
import json
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import SecretStr
from textual.binding import Binding
from textual.widgets import DataTable, Input, Static, TabbedContent

from ekb.config import Settings
from ekb.domain.models import AuthorityEvent, Scope, SemanticType
from ekb.interfaces.api import create_app
from ekb.interfaces.mcp import create_server
from ekb.interfaces.tui import KnowledgeApp
from ekb.runtime import Runtime

pytestmark = pytest.mark.integration


async def prepared(database, tmp_path):
    runtime = Runtime(
        Settings(
            data_dir=tmp_path / "runtime",
            database_url=SecretStr("postgresql+asyncpg://ekb:ekb@localhost/ekb"),
        )
    )
    await runtime.database.close()
    runtime.database = database
    from ekb.services.knowledge import KnowledgeService

    runtime.knowledge = KnowledgeService(database)
    await runtime.pipeline().scan(runtime.connectors(Path("tests/fixtures/local")))
    return runtime


async def test_web_and_api_share_real_persisted_provenance(database, tmp_path):
    runtime = await prepared(database, tmp_path)
    app = create_app(runtime)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/health")).json() == {"status": "ok"}
            inventory = await client.get("/")
            assert inventory.status_code == 200 and 'id="root"' in inventory.text
            assert inventory.text.index("scoutTheme") < inventory.text.index("/static/ui/app.js")
            for path in (
                "/company",
                "/architecture",
                "/platforms",
                "/knowledge",
                "/sources",
                "/review",
                "/ontology",
            ):
                page = await client.get(path)
                assert page.status_code == 200 and "/static/ui/app.js" in page.text
            renderer = await asyncio.to_thread(
                lambda: next(
                    Path("src", "ekb", "interfaces", "web", "static", "ui").glob(
                        "mermaid.core-*.js"
                    )
                )
            )
            for path in ("/static/ui/app.js", "/static/ui/app.css", f"/static/ui/{renderer.name}"):
                asset = await client.get(path)
                assert asset.status_code == 200 and asset.headers.get("etag")
                cached = await client.get(path, headers={"If-None-Match": asset.headers["etag"]})
                assert cached.status_code == 304
            assert (await client.get("/api/ui/config")).json() == {
                "governance_enabled": False,
            }
            assert (await client.get("/api/sources")).status_code == 200
            concepts = (await client.get("/api/concepts")).json()
            assert len(concepts) == 4
            first = (await client.get("/api/concepts?limit=2")).json()
            second = (await client.get(f"/api/concepts?limit=2&after={first[-1]['id']}")).json()
            assert [item["id"] for item in first + second] == [item["id"] for item in concepts]
            assert (
                await client.get(f"/api/concepts?limit=2&after={second[-1]['id']}")
            ).json() == []
            assert (await client.get("/api/concepts?after=invalid")).status_code == 422
            concept_id = concepts[0]["id"]
            detail = await client.get(f"/concepts/{concept_id}")
            assert detail.status_code == 200 and "/static/ui/app.js" in detail.text
            provenance = (await client.get(f"/api/concepts/{concept_id}/provenance")).json()
            assert (
                provenance
                and provenance[0]["occurrence"]["excerpt"] in provenance[0]["segment"]["text"]
            )
            assert (await client.get(f"/api/concepts/{uuid4()}")).status_code == 404
            assert (await client.get("/api/concepts?limit=500")).status_code == 422
            assert (await client.get(f"/concepts/{uuid4()}")).status_code == 404
            legacy = await client.get("/?q=logging", headers={"HX-Request": "true"})
            assert legacy.status_code == 200 and 'id="root"' in legacy.text
            runtime.settings.governance_token = SecretStr("synthetic-browser-test-token")
            configuration = await client.get("/api/ui/config")
            assert configuration.json()["governance_enabled"] is True
            assert "synthetic-browser-test-token" not in configuration.text
            runtime.settings.governance_token = SecretStr("")
            assert (await client.post("/scans")).status_code == 404
            assert (await client.get("/api/scans")).status_code == 404
            assert (await client.get("/api/tasks")).status_code == 404
            assert (await client.get("/api/statistics")).status_code == 404
            assert (await client.get("/api/pipeline/status")).status_code == 404
            assert (await client.get("/pipeline")).status_code == 404
            assert not hasattr(app.state, "scan_task")
    await runtime.__aexit__()


async def test_source_drilldown_is_evidence_bound_paged_and_readonly(database, tmp_path):
    from ekb.domain.models import SemanticVersion, now

    runtime = await prepared(database, tmp_path)
    app = create_app(runtime)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                sources = (await client.get("/api/sources")).json()
                details = [
                    (await client.get(f"/api/sources/{source['item']['id']}")).json()
                    for source in sources
                ]
                own = max(details, key=lambda source: source["versions"])
                assert own["versions"] >= 2
                source_id = own["source"]["item"]["id"]
                first = (await client.get(f"/api/sources/{source_id}?limit=1")).json()
                assert len(first["items"]) == 1 and first["next_cursor"]
                second = (
                    await client.get(
                        f"/api/sources/{source_id}?limit=1&after={first['next_cursor']}"
                    )
                ).json()
                assert first["items"][0]["version"]["id"] != second["items"][0]["version"]["id"]
                assert sum(item["evidence_count"] for item in own["items"]) == own["occurrences"]
                item = own["items"][0]
                concept = await runtime.knowledge.get(UUID(item["concept_id"]))
                assert concept
                original = next(v for v in concept.versions if str(v.id) == item["version"]["id"])
                later = original.model_copy(
                    update={
                        "id": uuid4(),
                        "predecessor_id": original.id,
                        "semantic_version": SemanticVersion(major=1, minor=0, patch=0),
                        "recorded_at": now(),
                        "candidate": original.candidate.model_copy(
                            update={
                                "statement": "A later interpretation without this source.",
                                "semantic_type": SemanticType.PLATFORM_REQUIREMENT,
                                "goal_level": None,
                                "scope": Scope(platform="Azure"),
                            }
                        ),
                    }
                )
                async with database() as uow:
                    await uow.knowledge.add_version(later)
                    await uow.commit()
                again = (await client.get(f"/api/sources/{source_id}")).json()
                assert later.id not in {UUID(row["version"]["id"]) for row in again["items"]}
                assert again["versions"] == own["versions"]
                filtered = (await client.get(f"/api/concepts?source_id={source_id}")).json()
                selected = next(row for row in filtered if row["id"] == item["concept_id"])
                assert selected["matching_version_id"] != str(later.id)
                assert str(original.id) in {
                    version_id
                    for citation in selected["sources"]
                    if citation["id"] == source_id
                    for version_id in citation["version_ids"]
                }
                assert (
                    await client.get(f"/api/concepts?source_id={source_id}&q=A%20later")
                ).json() == []
                implementations = (await client.get("/api/concepts?level=platform_specific")).json()
                implementation = next(
                    row for row in implementations if row["id"] == item["concept_id"]
                )
                assert implementation["matching_version_id"] == str(later.id)
                assert implementation["levels"][str(later.id)] == "platform_specific"
                assert (
                    await client.get(f"/api/concepts?level=platform_specific&source_id={source_id}")
                ).json() == []
                assert (await client.get("/api/concepts?semantic_type=PlatformRequirement")).json()
                assert (await client.get("/api/concepts?level=invalid")).status_code == 422
                assert (await client.get("/api/concepts?source_id=invalid")).status_code == 422
                assert (await client.get(f"/api/concepts?source_id={uuid4()}")).json() == []
                first_filtered = (
                    await client.get(f"/api/concepts?source_id={source_id}&limit=1")
                ).json()
                next_filtered = (
                    await client.get(
                        f"/api/concepts?source_id={source_id}&limit=1&after={first_filtered[-1]['id']}"
                    )
                ).json()
                assert len(first_filtered + next_filtered) == 2
                assert first_filtered[0]["id"] != next_filtered[0]["id"]
                assert (await client.get(f"/sources/{source_id}")).status_code == 200
                assert (await client.get(f"/api/sources/{uuid4()}")).status_code == 404
                assert (await client.get(f"/sources/{uuid4()}")).status_code == 404
                assert (await client.get(f"/api/sources/{source_id}?limit=51")).status_code == 422
    finally:
        await runtime.__aexit__()


async def test_sources_remain_independent_and_removed_vertical_routes_are_absent(
    database, tmp_path
):
    runtime = await prepared(database, tmp_path)
    app = create_app(runtime)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                originals = (await client.get("/api/sources")).json()
                assert len(originals) == 3
                for source in originals:
                    source_id = source["item"]["id"]
                    detail = (await client.get(f"/api/sources/{source_id}")).json()
                    assert detail["source"]["item"]["id"] == source_id
                    assert "group" not in detail
                assert (await client.get("/api/source-groups")).status_code == 404
                assert (await client.get("/api/benchmarks/compare")).status_code == 404
    finally:
        await runtime.__aexit__()


async def test_table_inspection_only_exposes_owned_tables_with_stable_readonly_links(
    database, tmp_path
):
    from sqlalchemy import text

    runtime = await prepared(database, tmp_path)
    async with database.engine.begin() as connection:
        await connection.execute(text("CREATE TABLE unrelated_private (secret text)"))
    app = create_app(runtime)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                catalog = (await client.get("/api/inspection/tables")).json()
                assert {table["name"] for table in catalog} >= {
                    "sources",
                    "occurrences",
                    "reconciliation_decisions",
                    "issue_events",
                }
                assert "unrelated_private" not in {table["name"] for table in catalog}
                first = (await client.get("/api/inspection/tables/segments?limit=1")).json()
                second = (
                    await client.get(
                        f"/api/inspection/tables/segments?limit=1&after={first['next_cursor']}"
                    )
                ).json()
                assert first["rows"][0]["id"] != second["rows"][0]["id"]
                assert (
                    next(c for c in first["columns"] if c["name"] == "revision_id")["reference"]
                    == "source_revisions"
                )
                exact = (
                    await client.get(
                        f"/api/inspection/tables/segments?record={first['rows'][0]['id']}"
                    )
                ).json()
                assert exact["rows"] == first["rows"]
                for name in ("unrelated_private", "pg_authid", "sources;DROP TABLE sources"):
                    assert (await client.get(f"/api/inspection/tables/{name}")).status_code == 404
                assert (
                    await client.get("/api/inspection/tables/segments?record=invalid")
                ).status_code == 404
                assert (
                    await client.get("/api/inspection/tables/sources?limit=1000")
                ).status_code == 422
                assert (
                    await client.post("/api/inspection/tables/sources", json={})
                ).status_code == 405
                for path in ("/data", "/logs", "/explore", "/reconciliation"):
                    assert (await client.get(path)).status_code == 200
    finally:
        await runtime.__aexit__()


async def test_log_inspection_omits_payloads_and_credentials_and_tolerates_partial_writes(
    database, tmp_path
):
    runtime = await prepared(database, tmp_path)
    directory = runtime.settings.data_dir / "telemetry" / "123"
    directory.mkdir(parents=True)
    (directory / "logs.jsonl").write_text(
        json.dumps(
            {
                "event": "source_failed",
                "timestamp": "2026-01-01T10:00:00Z",
                "error_type": "ValueError",
                "password": "do-not-serve",
                "message": "Sensitive document body",
                "payload": {"token": "do-not-serve"},
            }
        )
        + "\n"
        + json.dumps(
            {"event": "raw_payload", "timestamp": "2026-01-01T11:00:00Z", "token": "secret"}
        )
        + '\n{"unfinished":'
    )
    (directory / "traces.jsonl").write_text("Sensitive trace body")
    app = create_app(runtime)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/inspection/logs")
                assert response.status_code == 200
                assert response.json()["events"][0]["fields"] == {"error_type": "ValueError"}
                assert len(response.json()["events"]) == 1
                assert "Sensitive" not in response.text and "do-not-serve" not in response.text
                assert (await client.get("/api/inspection/logs?limit=201")).status_code == 422
    finally:
        await runtime.__aexit__()


async def test_readonly_mcp_protocol_returns_explicit_schemas_and_evidence(database, tmp_path):
    runtime = await prepared(database, tmp_path)
    server = create_server(runtime.knowledge)
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names == {
            "search_concepts",
            "get_concept",
            "trace_provenance",
            "find_related_requirements",
            "resolve_knowledge",
            "resolve_requirement",
            "list_knowledge_issues",
            "get_reconciliation_history",
        }
        assert all(tool.inputSchema and tool.outputSchema for tool in tools.tools)
        result = await session.call_tool("search_concepts", {"query": "logging", "limit": 10})
        assert not result.isError and result.structuredContent
        concepts = result.structuredContent["result"]
        assert concepts
        concept_id = concepts[0]["id"]
        provenance = await session.call_tool("trace_provenance", {"concept_id": concept_id})
        assert provenance.structuredContent
        item = provenance.structuredContent["result"][0]
        assert item["occurrence"]["excerpt"] in item["segment"]["text"]
        assert UUID(concept_id)
        strict = await session.call_tool("resolve_knowledge", {"concept_id": concept_id})
        assert not strict.isError and strict.structuredContent
        resolution = strict.structuredContent
        assert resolution["status"] == "not_established"
        assert not resolution["authoritative"] and not resolution["versions"]
        assert resolution["provenance"] and resolution["explanation"]
        invalid = await session.call_tool("get_concept", {"concept_id": "not-a-uuid"})
        assert invalid.isError
    await runtime.__aexit__()


async def test_tui_browses_sources_and_evidence_without_ingestion_controls(database, tmp_path):
    runtime = await prepared(database, tmp_path)
    app = KnowledgeApp(runtime)
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        assert app.query_one("#sources", DataTable).row_count == 3
        assert app.query_one("#concepts", DataTable).row_count == 4
        for selector in ("#scan", "#scans-pane", "#tasks-pane", "#statistics"):
            assert not app.query(selector)
        assert not hasattr(app, "action_scan")
        assert all(
            (binding.key if isinstance(binding, Binding) else binding[0]) != "s"
            for binding in app.BINDINGS
        )
        app.query_one(TabbedContent).active = "concepts-pane"
        app.query_one("#concepts", DataTable).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert '"provenance"' in str(app.query_one("#evidence", Static).content)
    await runtime.__aexit__()


async def test_publication_and_scoped_approval_are_distinct_across_interfaces(database, tmp_path):
    runtime = await prepared(database, tmp_path)
    directory = tmp_path / "draft"
    directory.mkdir()
    (directory / "standard.md").write_text(
        "# Synthetic draft standard\nVersion: 2.0.0-draft.1\n\n"
        "# Requirement\nOperators must retain synthetic draft audit logs."
    )
    report = await runtime.pipeline().scan(runtime.connectors(directory))
    assert report.failed == 0
    concept = (await runtime.knowledge.search("synthetic draft audit"))[0]
    async with database() as uow:
        await uow.knowledge.record_authority(
            AuthorityEvent(
                id=uuid4(),
                concept_version_id=concept.versions[-1].id,
                action="approve",
                actor="test-reviewer",
                reason="Synthetic pilot only",
                effective_at=date(2020, 1, 1),
                scope=Scope(environment="test"),
            )
        )
        await uow.commit()
    app = create_app(runtime)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = (await client.get(f"/api/concepts/{concept.id}")).json()
            assert response["versions"][-1]["publication"]["publication_status"] == "draft"
            assert response["versions"][-1]["semantic_version"] is None
            assert any(
                state["governance_status"] == "approved"
                and state["canonical_status"] == "not_canonical"
                and state["scope"]["environment"] == "test"
                for state in response["authority_states"]
            )
            page = (await client.get(f"/concepts/{concept.id}")).text
            assert "/static/ui/app.js" in page
    async with create_connected_server_and_client_session(
        create_server(runtime.knowledge)
    ) as session:
        result = await session.call_tool(
            "search_concepts", {"query": "synthetic draft audit", "limit": 10}
        )
        assert result.structuredContent
        record = result.structuredContent["result"][0]
        assert record["versions"][-1]["publication"]["publication_status"] == "draft"
        assert all(
            state["canonical_status"] == "not_canonical" for state in record["authority_states"]
        )
    terminal = KnowledgeApp(runtime)
    async with terminal.run_test(size=(140, 50)) as pilot:
        terminal.query_one("#search", Input).value = "synthetic draft audit"
        await terminal.action_reload()
        await pilot.pause()
        assert terminal.query_one("#concepts", DataTable).get_row(str(concept.id))[3] == "draft"
    await runtime.__aexit__()
