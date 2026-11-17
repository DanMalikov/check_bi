from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo
import math

from fastapi import HTTPException, Query
from app.config import settings
from app.repositories.clickhouse import repository


class Filters:
    def __init__(self, device_id: Annotated[str, Query(min_length=1, max_length=256)],
                 metric: Annotated[str, Query(min_length=1, max_length=256)],
                 start: datetime, end: datetime,
                 limit: Annotated[int | None, Query(ge=1, le=100000)] = None):
        if start.tzinfo is None or end.tzinfo is None:
            raise HTTPException(422, "start и end должны содержать timezone, например 2026-11-01T00:00:00Z.")
        if end <= start or end - start > timedelta(days=settings.api_max_range_days):
            raise HTTPException(422, f"Ожидается start < end; диапазон не более {settings.api_max_range_days} дней.")
        self.limit = limit if limit is not None else min(settings.api_default_rows, settings.api_max_rows)
        if self.limit > settings.api_max_rows:
            raise HTTPException(422, f"limit превышает API_MAX_ROWS={settings.api_max_rows}.")
        self.parameters = dict(device=device_id, metric=metric, start=int(start.timestamp()),
                               end=int(end.timestamp()), limit=self.limit + 1, tz=settings.clickhouse_timezone)


WHERE = """FROM {database}.metrics
WHERE device_id = {{device:String}} AND metric = {{metric:String}}
AND sent_at >= toDateTime({{start:Int64}}, {{tz:String}})
AND sent_at < toDateTime({{end:Int64}}, {{tz:String}})"""


def query_metrics(filters: Filters, kind: str,
                  bucket: Literal["minute", "hour", "day"] = "hour",
                  aggregate: Literal["avg", "min", "max", "sum", "count"] = "avg"):
    where = WHERE.format(database=settings.clickhouse_database)
    params = dict(filters.parameters)
    if kind == "aggregate":
        intervals = {"minute": "MINUTE", "hour": "HOUR", "day": "DAY"}
        # Both expressions come exclusively from fixed allowlists.
        funcs = {"avg": "avg(value_num)", "min": "min(value_num)", "max": "max(value_num)",
                 "sum": "sum(value_num)", "count": "count()"}
        sql = (f"SELECT toStartOfInterval(sent_at, INTERVAL 1 {intervals[bucket]}, {{tz:String}}) AS time, "
               f"{funcs[aggregate]} AS value, count() AS samples {where} AND value_num IS NOT NULL "
               "GROUP BY time ORDER BY time LIMIT {limit:UInt32}")
    elif kind == "series":
        sql = ("SELECT sent_at AS time, value_num AS value " + where +
               " AND value_num IS NOT NULL ORDER BY sent_at, value_num LIMIT {limit:UInt32}")
    else:
        sql = ("SELECT sent_at, device_id, metric, serial_number, model, product, value_str, value_num " + where +
               " ORDER BY sent_at, serial_number, model, product, value_str, value_num LIMIT {limit:UInt32}")
    rows = repository.select(sql, params)
    truncated = len(rows) > filters.limit
    rows = rows[:filters.limit]
    for row in rows:
        for key, value in row.items():
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=ZoneInfo(settings.clickhouse_timezone))
                row[key] = value.astimezone(timezone.utc).isoformat()
            elif isinstance(value, float) and not math.isfinite(value):
                row[key] = None
    return rows, truncated
