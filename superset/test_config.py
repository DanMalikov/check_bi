"""Check dialect and environment URI handling without contacting any database."""
import os
from unittest.mock import patch
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine
from superset.app import create_app
from superset_config import benchmark_clickhouse_uri

app = create_app()
with app.app_context(), patch.dict(os.environ, {
    "CLICKHOUSE_HOST": "example.invalid", "CLICKHOUSE_USER": "readonly",
    "CLICKHOUSE_PASSWORD": "test$#@:/password", "CLICKHOUSE_HTTP_PORT": "8443",
    "CLICKHOUSE_SECURE": "true",
}):
    from superset.models.core import Database
    database = Database(database_name="Transient test only")
    database.set_sqlalchemy_uri(benchmark_clickhouse_uri())
    url = make_url(database.sqlalchemy_uri_decrypted)
    assert url.password == os.environ["CLICKHOUSE_PASSWORD"]
    assert url.host == "example.invalid" and url.port == 8443
    engine = create_engine(url)
    assert engine.dialect.name == "clickhousedb"
    engine.dispose()
    print("PASS environment URI, escaped password callback and ClickHouse dialect; no external connection or persisted connection record")
