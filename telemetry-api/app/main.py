from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import settings
from app.api.grafana import router as metrics_router
from app.api.superset import router as superset_router
from app.api.benchmark import router as benchmark_router
from app.api.benchmark_jobs import router as jobs_router
from app.repositories.clickhouse import repository


@asynccontextmanager
async def lifespan(app):
    yield
    repository.close()


app = FastAPI(title="Telemetry Benchmark API", lifespan=lifespan)
app.include_router(metrics_router)
app.include_router(superset_router)
app.include_router(benchmark_router)
app.include_router(jobs_router)


@app.get("/health")
def health():
    return {"status": "ok", "clickhouse_configured": bool(settings.clickhouse_host and settings.clickhouse_user)}


@app.get("/api/health/clickhouse")
def clickhouse_health():
    repository.select(f"SELECT device_id, metric, sent_at FROM {settings.clickhouse_database}.metrics LIMIT 1")
    return {"status": "ok", "table": f"{settings.clickhouse_database}.metrics"}


@app.get("/api/config")
def public_config():
    return {"supersetDomain": settings.superset_public_url,
            "supersetConfigured": bool(settings.superset_embedded_dashboard_id),
            "grafanaInfinity": settings.grafana_infinity_dashboard_url,
            "grafanaDirect": settings.grafana_direct_dashboard_url}
