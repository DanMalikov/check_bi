"""Inspect response contracts in memory. Reports contain counts and hashes, not telemetry."""
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from urllib.parse import urlsplit, parse_qs

ROLES = ("hourly", "raw")
RAW_FIELDS = ("sent_at", "device_id", "metric", "value_num", "value_str")


class InvalidRun(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def sql_role(sql):
    compact = re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()
    # Fail closed: benchmark SQL is intentionally small. No rewriting or appended settings.
    hourly = r"select tostartofhour\(sent_at\) (?:as )?time,\s*count\(\) (?:as )?value from telemetry\.metrics group by time order by time"
    raw = r"select sent_at,\s*device_id,\s*metric,\s*value_num,\s*value_str from telemetry\.metrics order by sent_at,\s*device_id,\s*metric limit 100000"
    if re.fullmatch(hourly, compact):
        return "hourly"
    if re.fullmatch(raw, compact):
        return "raw"
    if re.search(r"\b(where|having|prewhere)\b", compact):
        raise InvalidRun("filtered_query")
    raise InvalidRun("unsupported_query")


def grafana_role(query, variant):
    if query.get("hide"):
        raise InvalidRun("unsupported_query")
    if variant == "direct":
        if query.get("datasource", {}).get("type") != "grafana-clickhouse-datasource":
            raise InvalidRun("unsupported_query")
        return sql_role(query.get("rawSql", ""))
    if query.get("parser") != "backend" or query.get("datasource", {}).get("type") != "yesoreyeram-infinity-datasource":
        raise InvalidRun("unsupported_query")
    if any(query.get(k) for k in ("filters", "filterExpression", "summarizeExpression", "computed_columns")):
        raise InvalidRun("filtered_query")
    if query.get("root_selector", "") not in ("", "$"):
        raise InvalidRun("unsupported_query")
    url = urlsplit(query.get("url", ""))
    params = parse_qs(url.query)
    options = query.get("url_options", {})
    if options.get("method", "GET") != "GET" or options.get("params") or options.get("headers"):
        raise InvalidRun("unsupported_query")
    if url.path == "/api/benchmark/events-hourly" and not params:
        return "hourly"
    if url.path == "/api/benchmark/rows" and params == {"limit": ["100000"]}:
        return "raw"
    raise InvalidRun("unsupported_query")


def superset_role(payload):
    queries = payload.get("queries", [])
    if len(queries) != 1:
        raise InvalidRun("unsupported_query")
    query = queries[0]
    for f in query.get("filters", []):
        if not (f.get("op") == "TEMPORAL_RANGE" and f.get("val") in (None, "No filter")):
            raise InvalidRun("filtered_query")
    if query.get("time_range") not in (None, "No filter") or query.get("time_offsets"):
        raise InvalidRun("filtered_query")
    if any(query.get("extras", {}).get(k) for k in ("where", "having")):
        raise InvalidRun("filtered_query")
    columns = query.get("columns", [])
    if columns == list(RAW_FIELDS) and not query.get("metrics"):
        if query.get("row_limit") != 100000:
            raise InvalidRun("row_count")
        order = query.get("orderby", [])
        if order[:3] != [["sent_at", True], ["device_id", True], ["metric", True]]:
            raise InvalidRun("unsupported_query")
        return "raw"
    if (query.get("metrics") == ["count"] and len(columns) == 1
            and query.get("extras", {}).get("time_grain_sqla") == "PT1H"):
        return "hourly"
    raise InvalidRun("unsupported_query")


def timestamp(value):
    if isinstance(value, (int, float)):
        # Grafana time fields and Superset epoch responses use milliseconds.
        return int(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                # Refuse ambiguous time; do not silently assume a server timezone.
                raise InvalidRun("invalid_data")
            return int(dt.timestamp() * 1000)
        except ValueError:
            raise InvalidRun("invalid_data") from None
    raise InvalidRun("invalid_data")


def numeric(value):
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise InvalidRun("invalid_data")
    return format(value, ".17g") if math.isfinite(value) else None


def fingerprint(role, rows):
    if role == "raw" and len(rows) != 100000:
        raise InvalidRun("row_count")
    if role == "hourly" and (not rows or len(rows) > 200000):
        raise InvalidRun("invalid_data")
    canonical = []
    seen = set()
    for row in rows:
        if role == "hourly":
            t = timestamp(row["time"])
            value = row["value"]
            if t in seen or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 or int(value) != value:
                raise InvalidRun("invalid_data")
            seen.add(t)
            data = [t, int(value)]
        else:
            if not all(k in row for k in RAW_FIELDS) or not all(isinstance(row[k], str) for k in ("device_id", "metric", "value_str")):
                raise InvalidRun("invalid_data")
            data = [timestamp(row["sent_at"]), row["device_id"], row["metric"], numeric(row["value_num"]), row["value_str"]]
        canonical.append(hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()).digest())
    # Multiset: independent of display sort order, preserves duplicate row multiplicity.
    digest = hashlib.sha256(b"".join(sorted(canonical))).hexdigest()
    return {"rows": len(rows), "fingerprint": digest}


def superset_data(payload, role):
    result = payload.get("result", [])
    if len(result) != 1 or result[0].get("error") or result[0].get("status") not in (None, "success"):
        raise InvalidRun("query_error")
    block = result[0]
    if re.search(r"\b(where|having|prewhere)\b", block.get("query") or "", re.IGNORECASE):
        raise InvalidRun("filtered_query")
    data = block.get("data")
    if not isinstance(data, list) or len(data) > 200000:
        raise InvalidRun("invalid_data")
    rows = []
    for row in data:
        if role == "hourly":
            fields = {key(k): v for k, v in row.items()}
            t = fields.get("sentat", fields.get("time", fields.get("timestamp")))
            v = fields.get("count", fields.get("value"))
            rows.append({"time": t, "value": v})
        else:
            rows.append(row)
    return fingerprint(role, rows), bool(block.get("is_cached"))


def grafana_data(payload, queries, variant):
    blocks = payload.get("results")
    if not isinstance(blocks, dict):
        raise InvalidRun("invalid_data")
    output = {}
    for query in queries:
        role = grafana_role(query, variant)
        block = blocks.get(query.get("refId", "A"), {})
        if block.get("error") or block.get("status", 200) >= 400:
            raise InvalidRun("query_error")
        rows = []
        for frame in block.get("frames", []):
            schema = frame.get("schema", {})
            if schema.get("meta", {}).get("notices"):
                raise InvalidRun("invalid_data")
            fields, values = schema.get("fields", []), frame.get("data", {}).get("values", [])
            if not fields or len(fields) != len(values) or len({len(c) for c in values}) != 1:
                raise InvalidRun("invalid_data")
            if len(values[0]) > 200000:
                raise InvalidRun("invalid_data")
            aliases = {key(c.get("text", c.get("selector"))): c.get("selector") for c in query.get("columns", [])}
            names = [aliases.get(key(f["name"]), f["name"]) for f in fields]
            for cells in zip(*values):
                row = dict(zip(names, cells))
                if role == "hourly":
                    normalized = {key(k): v for k, v in row.items()}
                    row = {"time": normalized.get("time"), "value": normalized.get("value")}
                rows.append(row)
        if role in output:
            raise InvalidRun("unsupported_query")
        output[role] = fingerprint(role, rows)
    return output
