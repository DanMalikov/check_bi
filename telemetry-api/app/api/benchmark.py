from datetime import datetime, timezone
import math
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response

from app.config import settings
from app.repositories.clickhouse import repository


router = APIRouter(prefix="/api/benchmark", tags=["Benchmark / Infinity"])


def json_rows(response: Response, rows):
    for row in rows:
        for key, value in row.items():
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=ZoneInfo(settings.clickhouse_timezone))
                row[key] = value.astimezone(timezone.utc).isoformat()
            elif isinstance(value, float) and not math.isfinite(value):
                row[key] = None
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Result-Rows"] = str(len(rows))
    return rows


@router.get("/events-hourly")
def events_hourly(response: Response):
    rows = repository.select(
        f"SELECT toStartOfHour(sent_at) AS time, count() AS value "
        f"FROM {settings.clickhouse_database}.metrics GROUP BY time ORDER BY time"
    )
    return json_rows(response, rows)


@router.get("/rows")
def rows(response: Response, limit: Annotated[int | None, Query(ge=1, le=100000)] = None):
    if limit is None:
        limit = min(settings.api_default_rows, settings.api_max_rows)
    if limit > settings.api_max_rows:
        raise HTTPException(422, f"limit превышает API_MAX_ROWS={settings.api_max_rows}.")
    records = repository.select(
        f"SELECT sent_at, device_id, metric, value_num, value_str "
        f"FROM {settings.clickhouse_database}.metrics "
        "ORDER BY sent_at, device_id, metric LIMIT {limit:UInt32}",
        {"limit": limit},
    )
    return json_rows(response, records)

@router.get("/multiseries")
def multiseries(response: Response):
    sql = f"""
        SELECT
            toStartOfInterval(sent_at, INTERVAL 6 HOUR) AS time,
            concat(metric, ' | ', device_id) AS series,
            avg(value_num) AS value
        FROM {settings.clickhouse_database}.metrics
        WHERE metric IN (
            'battery_voltage',
            'printhead_temperature',
            'power_adapter_input_voltage'
        )
        AND value_num IS NOT NULL
        GROUP BY
            time,
            metric,
            device_id
        ORDER BY
            time,
            series
        LIMIT 100000
    """

    rows = repository.select(sql)
    return json_rows(response, rows)