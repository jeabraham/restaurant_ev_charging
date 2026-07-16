from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

# Ensure required env vars are present for tests that start the app lifespan.
# These are sentinel values; real API calls are mocked with respx.
os.environ.setdefault("OPENCHARGEMAP_API_KEY", "test_ocm_key")
os.environ.setdefault("GEOAPIFY_API_KEY", "test_geo_key")


@pytest.fixture
async def test_client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
