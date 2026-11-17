import pytest
from benchmark.contracts import InvalidRun, fingerprint, grafana_role, grafana_data, superset_role, sql_role

HOURLY = "SELECT toStartOfHour(sent_at) AS time, count() AS value FROM telemetry.metrics GROUP BY time ORDER BY time;"
RAW = "SELECT sent_at, device_id, metric, value_num, value_str FROM telemetry.metrics ORDER BY sent_at, device_id, metric LIMIT 100000;"


def test_sql_contract_without_modifying_queries():
    assert sql_role(HOURLY) == "hourly"
    assert sql_role(RAW) == "raw"
    with pytest.raises(InvalidRun, match="filtered_query"):
        sql_role(RAW.replace("ORDER BY", "WHERE metric='battery_voltage' ORDER BY"))


def test_superset_extra_filter_and_sort_are_rejected():
    q = {"columns": ["sent_at", "device_id", "metric", "value_num", "value_str"], "row_limit": 100000, "filters": [{"col": "metric", "op": "==", "val": "battery_voltage"}]}
    with pytest.raises(InvalidRun, match="filtered_query"):
        superset_role({"queries": [q]})
    q["filters"] = []
    with pytest.raises(InvalidRun, match="unsupported_query"):
        superset_role({"queries": [q]})
    q["orderby"] = [["sent_at", True], ["device_id", True], ["metric", True]]
    assert superset_role({"queries": [q]}) == "raw"


def test_fingerprint_compares_actual_rows_without_saving_them():
    rows = [{"sent_at": 1761955200000, "device_id": "fixture", "metric": "fixture", "value_num": None, "value_str": "fixture"}] * 100000
    first = fingerprint("raw", rows)
    assert first["rows"] == 100000 and len(first["fingerprint"]) == 64
    rows[-1] = {**rows[-1], "value_num": 1}
    assert fingerprint("raw", rows)["fingerprint"] != first["fingerprint"]
    with pytest.raises(InvalidRun, match="row_count"):
        fingerprint("raw", rows[:99999])


def test_empty_error_or_misaligned_frames_are_not_success():
    query = {"datasource": {"type": "grafana-clickhouse-datasource"}, "rawSql": HOURLY, "refId": "A"}
    with pytest.raises(InvalidRun, match="query_error"):
        grafana_data({"results": {"A": {"error": "error"}}}, [query], "direct")
    with pytest.raises(InvalidRun, match="invalid_data"):
        grafana_data({"results": {"A": {"frames": []}}}, [query], "direct")


def test_infinity_backend_uses_grafana_payload_not_direct_api_timing():
    q = {"datasource": {"type": "yesoreyeram-infinity-datasource"}, "parser": "backend", "root_selector": "$", "url": "http://telemetry-api:8000/api/benchmark/events-hourly", "refId": "A", "columns": [{"selector": "time", "text": "Time"}, {"selector": "value", "text": "Value"}]}
    payload = {"results": {"A": {"frames": [{"schema": {"fields": [{"name": "Time"}, {"name": "Value"}]}, "data": {"values": [[1761955200000], [10]]}}]}}}
    assert grafana_data(payload, [q], "infinity")["hourly"]["rows"] == 1
    q["parser"] = "uql"
    with pytest.raises(InvalidRun):
        grafana_role(q, "infinity")
