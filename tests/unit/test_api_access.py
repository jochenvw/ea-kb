import httpx
import pytest

from ekb.interfaces.api import create_app


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
            "/pipeline",
            "/benchmarks",
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
