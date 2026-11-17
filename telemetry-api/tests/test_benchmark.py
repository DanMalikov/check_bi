from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.config import settings
from test_api import get


def test_hourly_counts_entire_table_without_filters():
    with patch("app.api.benchmark.repository.select", return_value=[
        {"time": datetime(2026, 11, 1, tzinfo=timezone.utc), "value": 25},
    ]) as select:
        response = get("/api/benchmark/events-hourly")
    assert response.status_code == 200
    assert response.json() == [{"time": "2026-11-01T00:00:00+00:00", "value": 25}]
    sql = " ".join(select.call_args.args[0].split())
    assert sql == (f"SELECT toStartOfHour(sent_at) AS time, count() AS value "
                   f"FROM {settings.clickhouse_database}.metrics GROUP BY time ORDER BY time")
    assert response.headers["Cache-Control"] == "no-store"


def test_raw_100000_rows_parameterized_without_filters(monkeypatch):
    monkeypatch.setattr(settings, "api_max_rows", 100000)
    with patch("app.api.benchmark.repository.select", return_value=[
        {"sent_at": datetime(2026, 11, 1, tzinfo=timezone.utc), "device_id": "d1",
         "metric": "state", "value_num": None, "value_str": "ready"},
    ]) as select:
        response = get("/api/benchmark/rows", {"limit": 100000})
    assert response.status_code == 200
    assert response.json() == [{"sent_at": "2026-11-01T00:00:00+00:00", "device_id": "d1",
                                "metric": "state", "value_num": None, "value_str": "ready"}]
    sql, params = select.call_args.args
    assert sql == (f"SELECT sent_at, device_id, metric, value_num, value_str "
                   f"FROM {settings.clickhouse_database}.metrics "
                   "ORDER BY sent_at, device_id, metric LIMIT {limit:UInt32}")
    assert params == {"limit": 100000}


@pytest.mark.parametrize("limit", [0, -1, 100001, "1; DROP TABLE metrics", 101])
def test_invalid_limit_does_not_query_clickhouse(monkeypatch, limit):
    monkeypatch.setattr(settings, "api_max_rows", 100)
    with patch("app.api.benchmark.repository.select") as select:
        assert get("/api/benchmark/rows", {"limit": limit}).status_code == 422
        select.assert_not_called()


def test_default_limit_respects_configuration_and_empty_response(monkeypatch):
    monkeypatch.setattr(settings, "api_default_rows", 1000)
    monkeypatch.setattr(settings, "api_max_rows", 100)
    with patch("app.api.benchmark.repository.select", return_value=[]) as select:
        response = get("/api/benchmark/rows")
    assert response.status_code == 200 and response.json() == []
    assert select.call_args.args[1] == {"limit": 100}
