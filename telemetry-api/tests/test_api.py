from datetime import datetime, timezone
import asyncio
from unittest.mock import patch

import httpx
import pytest
from app.main import app
from app.config import settings


def get(path, params=None):
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(path, params=params)
    return asyncio.run(request())


BASE = dict(device_id="device-1", metric="battery_voltage", start="2026-11-01T00:00:00Z", end="2026-11-02T00:00:00Z")


@pytest.mark.parametrize("change", [
    {"start": "2026-11-03T00:00:00Z"}, {"start": "2026-11-01T00:00:00"},
    {"limit": 100001}, {"device_id": ""}, {"end": "2030-01-01T00:00:00Z"},
])
def test_reject_invalid_filters(change):
    with patch("app.services.metrics.repository.select") as select:
        assert get("/api/metrics/rows", BASE | change).status_code == 422
        select.assert_not_called()


def test_values_are_bound_and_truncation_is_reported():
    malicious = "x' OR 1=1 --"
    rows = [{"time": datetime(2026, 11, 1, tzinfo=timezone.utc), "value": 1.0}] * 3
    with patch("app.services.metrics.repository.select", return_value=rows) as select:
        response = get("/api/metrics/series", BASE | {"metric": malicious, "limit": 2})
        assert response.status_code == 200
        assert len(response.json()) == 2
        assert response.headers["X-Result-Truncated"] == "true"
        sql, params = select.call_args.args
        assert malicious not in sql
        assert params["metric"] == malicious and params["limit"] == 3


def test_no_arbitrary_aggregation_or_bucket():
    with patch("app.services.metrics.repository.select") as select:
        assert get("/api/metrics/aggregate", BASE | {"aggregation": "DROP"}).status_code == 422
        assert get("/api/metrics/aggregate", BASE | {"bucket": "1; DROP"}).status_code == 422
        select.assert_not_called()


def test_unconfigured_database_does_not_break_health(monkeypatch):
    monkeypatch.setattr(settings, "clickhouse_host", "")
    assert get("/health").status_code == 200
    response = get("/api/health/clickhouse")
    assert response.status_code == 503
    assert "CLICKHOUSE_HOST" in response.json()["detail"]


def test_missing_dashboard_and_public_config(monkeypatch):
    monkeypatch.setattr(settings, "superset_embedded_dashboard_id", "")
    assert get("/api/superset/embed").status_code == 409
    config = get("/api/config").json()
    assert not any("password" in key.lower() or "secret" in key.lower() for key in config)


def test_real_superset_protocol_with_mock_transport(monkeypatch):
    from app.clients import superset
    seen = []
    real_client = httpx.AsyncClient

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"access_token": "access"}, headers={"set-cookie": "session=s; Path=/"})
        if request.url.path.endswith("/csrf_token/"):
            return httpx.Response(200, json={"result": "csrf"})
        assert request.headers["Authorization"] == "Bearer access"
        assert request.headers["X-CSRFToken"] == "csrf"
        assert "session=s" in request.headers["cookie"]
        return httpx.Response(200, json={"token": "from-superset"})

    def factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(superset.httpx, "AsyncClient", factory)
    assert asyncio.run(superset.guest_token("dashboard-uuid")) == "from-superset"
    assert len(seen) == 3
