"""Integration tests for the webhook endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from health_coach.main import create_app
from health_coach.settings import Settings


@pytest.fixture
def app() -> MagicMock:
    """Create a test app with mocked dependencies."""
    settings = Settings(app_mode="api")
    app = create_app(settings)

    # Mock session factory that returns no existing processed events
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)

    mock_sf = MagicMock()
    mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

    app.state.session_factory = mock_sf
    app.state.engine = MagicMock()
    app.state.graph = AsyncMock()
    app.state.ctx_factory = MagicMock(return_value=MagicMock())

    return app


@pytest.mark.asyncio
async def test_webhook_missing_fields(app: MagicMock) -> None:
    """Webhook returns 400 for missing required fields."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/medbridge",
            json={},
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_webhook_processes_event(app: MagicMock) -> None:
    """Webhook processes valid events."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/medbridge",
            json={
                "event_type": "patient_login",
                "event_id": "evt-123",
                "tenant_id": "t1",
                "patient_id": "p1",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "processed"
