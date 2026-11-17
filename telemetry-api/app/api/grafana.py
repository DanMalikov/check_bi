from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Response
from app.services.metrics import Filters, query_metrics

router = APIRouter(prefix="/api/metrics", tags=["Telemetry / Infinity"])
FilterDependency = Annotated[Filters, Depends()]


def result(response, filters, kind, **kwargs):
    rows, truncated = query_metrics(filters, kind, **kwargs)
    response.headers["X-Result-Truncated"] = str(truncated).lower()
    response.headers["X-Result-Rows"] = str(len(rows))
    response.headers["Cache-Control"] = "no-store"
    return rows


@router.get("/series")
def series(response: Response, filters: FilterDependency):
    return result(response, filters, "series")


@router.get("/aggregate")
def aggregate(response: Response, filters: FilterDependency,
              bucket: Literal["minute", "hour", "day"] = "hour",
              aggregation: Literal["avg", "min", "max", "sum", "count"] = "avg"):
    return result(response, filters, "aggregate", bucket=bucket, aggregate=aggregation)


@router.get("/rows")
def rows(response: Response, filters: FilterDependency):
    return result(response, filters, "rows")
