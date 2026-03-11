"""Tests for health check endpoints."""

from httpx import AsyncClient


async def test_liveness(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readiness_sqlite(client: AsyncClient) -> None:
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["langgraph_pool"] == "not_configured"
