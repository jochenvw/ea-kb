import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import SecretStr
from textual.widgets import DataTable, Static, TabbedContent

from ekb.config import Settings
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
            assert inventory.status_code == 200 and "Knowledge inventory" in inventory.text
            assert (await client.get("/static/htmx.min.js")).status_code == 200
            assert (await client.get("/api/sources")).status_code == 200
            concepts = (await client.get("/api/concepts")).json()
            assert len(concepts) == 4
            concept_id = concepts[0]["id"]
            detail = await client.get(f"/concepts/{concept_id}")
            assert detail.status_code == 200 and "Exact evidence and provenance" in detail.text
            provenance = (await client.get(f"/api/concepts/{concept_id}/provenance")).json()
            assert (
                provenance
                and provenance[0]["occurrence"]["excerpt"] in provenance[0]["segment"]["text"]
            )
            assert (await client.get(f"/api/concepts/{uuid4()}")).status_code == 404
            assert (await client.get("/api/concepts?limit=500")).status_code == 422
            partial = await client.get("/", headers={"HX-Request": "true"})
            assert "<html" not in partial.text and 'id="inventory"' in partial.text
            denied = await client.post("/scans", headers={"Origin": "https://other.example"})
            assert denied.status_code == 403
            assert (await client.get("/api/statistics")).json()["task_count"] == 3
            runtime.settings.source_directory = Path("tests/fixtures/local")
            started = await client.post("/scans", headers={"Origin": "http://test"})
            assert started.status_code == 303
            await asyncio.wait_for(app.state.scan_task, timeout=10)
            assert (await client.get("/api/scans")).json()[0]["skipped"] == 3
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
        invalid = await session.call_tool("get_concept", {"concept_id": "not-a-uuid"})
        assert invalid.isError
    await runtime.__aexit__()


async def test_tui_browses_sources_and_stays_responsive_during_scan(database, tmp_path):
    runtime = await prepared(database, tmp_path)
    app = KnowledgeApp(runtime, Path("tests/fixtures/local"))
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        assert app.query_one("#sources", DataTable).row_count == 3
        assert app.query_one("#concepts", DataTable).row_count == 4
        app.query_one(TabbedContent).active = "concepts-pane"
        app.query_one("#concepts", DataTable).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert '"provenance"' in str(app.query_one("#evidence", Static).content)
        app.action_scan()
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert "skipped" in str(app.query_one("#status", Static).content)
    await runtime.__aexit__()
