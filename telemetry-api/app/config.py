from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")
    clickhouse_host: str = ""
    clickhouse_http_port: int = 8123
    clickhouse_database: str = Field("telemetry", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    clickhouse_user: str = ""
    clickhouse_password: str = ""
    clickhouse_secure: bool = False
    clickhouse_verify: bool = True
    clickhouse_connect_timeout: int = Field(5, ge=1, le=60)
    clickhouse_query_timeout: int = Field(30, ge=1, le=600)
    clickhouse_timezone: str = "UTC"
    api_default_rows: int = Field(1000, ge=1, le=100000)
    api_max_rows: int = Field(10000, ge=1, le=100000)
    api_max_range_days: int = Field(180, ge=1, le=3660)
    superset_internal_url: str = "http://superset:8088"
    superset_public_url: str = "http://localhost:8088"
    superset_embedded_dashboard_id: str = ""
    superset_tech_user: str = "embed_service"
    superset_tech_password: str = ""
    superset_api_timeout: int = 30
    grafana_infinity_dashboard_url: str = ""
    grafana_direct_dashboard_url: str = ""


settings = Settings()
