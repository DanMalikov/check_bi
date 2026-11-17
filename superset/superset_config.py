import os
from sqlalchemy.engine import URL

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = "sqlite:////app/superset_home/superset.db"
SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
FEATURE_FLAGS = {"EMBEDDED_SUPERSET": True}
GUEST_TOKEN_JWT_SECRET = os.environ["SUPERSET_GUEST_TOKEN_SECRET"]
GUEST_TOKEN_JWT_EXP_SECONDS = int(os.getenv("SUPERSET_GUEST_TOKEN_TTL", "300"))
GUEST_ROLE_NAME = "BenchmarkGuest"
WTF_CSRF_ENABLED = True
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = "Lax"
TALISMAN_ENABLED = True
TALISMAN_CONFIG = {
    "force_https": False,
    "session_cookie_secure": False,
    "frame_options": None,
    "content_security_policy": {
        "default-src": ["'self'"],
        "img-src": ["'self'", "data:", "blob:"],
        "worker-src": ["'self'", "blob:"],
        "connect-src": ["'self'"],
        "style-src": ["'self'", "'unsafe-inline'"],
        "script-src": ["'self'", "'strict-dynamic'"],
        "frame-ancestors": ["'self'", os.getenv("ADMIN_ORIGIN", "http://localhost:5173")],
    },
    "content_security_policy_nonce_in": ["script-src"],
}
# No background workers or external cache required for synchronous local charts.
CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 0}
DATA_CACHE_CONFIG = {"CACHE_TYPE": "NullCache"}
THUMBNAILS_SQLA_LISTENERS = False
ENABLE_PROXY_FIX = False
SUPERSET_WEBSERVER_TIMEOUT = 120


def SQLALCHEMY_CUSTOM_PASSWORD_STORE(url):
    """Keep the external password in environment, not in a copied connection URI."""
    if url.drivername == "clickhousedb":
        return os.getenv("CLICKHOUSE_PASSWORD", "")
    return None


def benchmark_clickhouse_uri():
    """Used by the documented command; never connects or creates a dataset."""
    host = os.getenv("CLICKHOUSE_HOST", "")
    user = os.getenv("CLICKHOUSE_USER", "")
    if not host or not user:
        raise ValueError("Заполните CLICKHOUSE_HOST и CLICKHOUSE_USER в .env")
    return URL.create(
        "clickhousedb", username=user, password=None, host=host,
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        database=os.getenv("CLICKHOUSE_DATABASE", "telemetry"),
        query={"secure": os.getenv("CLICKHOUSE_SECURE", "false"),
               "verify": os.getenv("CLICKHOUSE_VERIFY", "true"),
               "connect_timeout": os.getenv("CLICKHOUSE_CONNECT_TIMEOUT", "5"),
               "send_receive_timeout": os.getenv("CLICKHOUSE_QUERY_TIMEOUT", "30")},
    ).render_as_string(hide_password=False)
