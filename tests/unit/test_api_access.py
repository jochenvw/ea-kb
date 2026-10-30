import asyncio
from typing import cast
from unittest.mock import Mock

import httpx
import pytest

from ekb.config import Settings
from ekb.interfaces.api import create_app
from ekb.runtime import Runtime


@pytest.mark.asyncio
async def test_web_and_data_routes_reject_remote_and_forwarded_requests():
    app = create_app()
    transport = httpx.ASGITransport(app=app, client=("203.0.113.7", 41000))
    async with httpx.AsyncClient(transport=transport, base_url="http://ekb.test") as client:
        for path in ("/", "/api/ui/config", "/api/sources"):
            response = await client.get(path)
            assert response.status_code == 403

    forwarded_transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41000))
    async with httpx.AsyncClient(
        transport=forwarded_transport, base_url="http://ekb.test"
    ) as client:
        response = await client.get("/api/sources", headers={"x-forwarded-for": "203.0.113.7"})
        assert response.status_code == 403

    loopback_transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41000))
    async with httpx.AsyncClient(
        transport=loopback_transport, base_url="http://ekb.test"
    ) as client:
        response = await client.get("/")
        assert response.status_code == 200


async def test_workspace_routes_serve_the_prebuilt_shell_without_templates():
    app = create_app()
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41000))
    async with httpx.AsyncClient(transport=transport, base_url="http://ekb.test") as client:
        shell = await client.get("/static/ui/index.html")
        assert shell.status_code == 200
        for path in (
            "/",
            "/knowledge",
            "/sources",
            "/explore",
            "/reconciliation",
            "/data",
            "/logs",
            "/ontology",
            "/review",
        ):
            response = await client.get(path)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/html")
            assert response.content == shell.content
        assert (await client.get("/review?status=invalid")).status_code == 422
        assert (await client.get("/review?finding_id=invalid")).status_code == 422


async def test_ingestion_and_legacy_benchmark_routes_and_monitoring_schemas_are_absent():
    app = create_app()
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41000))
    async with httpx.AsyncClient(transport=transport, base_url="http://ekb.test") as client:
        for path in (
            "/pipeline",
            "/benchmarks",
            "/api/benchmarks/compare",
            "/api/source-groups",
            "/api/scans",
            "/api/tasks",
            "/api/statistics",
            "/api/pipeline/status",
        ):
            assert (await client.get(path)).status_code == 404
        assert (await client.post("/scans")).status_code == 404
        schema = (await client.get("/openapi.json")).json()
        assert "/scans" not in schema["paths"]
        assert "/api/benchmarks/compare" not in schema["paths"]
        assert not {"ScanReport", "ProcessingTask", "Statistics"} & set(
            schema["components"]["schemas"]
        )


async def test_web_lifespan_neither_starts_nor_cancels_ingestion(tmp_path):
    runtime = Mock(spec=Runtime)
    runtime.settings = Settings(data_dir=tmp_path)
    runtime.pipeline.side_effect = AssertionError("Web must not run ingestion")
    runtime.connectors.side_effect = AssertionError("Web must not select ingestion sources")
    app = create_app(cast(Runtime, runtime))
    release = asyncio.Event()
    external = asyncio.create_task(release.wait())
    try:
        async with app.router.lifespan_context(app):
            assert not hasattr(app.state, "scan_task")
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41000))
            async with httpx.AsyncClient(transport=transport, base_url="http://ekb.test") as client:
                assert (await client.get("/api/ui/config")).json() == {
                    "governance_enabled": bool(runtime.settings.governance_token.get_secret_value())
                }
        assert not external.done()
        runtime.pipeline.assert_not_called()
        runtime.connectors.assert_not_called()
    finally:
        release.set()
        await external
