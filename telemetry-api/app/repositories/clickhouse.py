from contextlib import contextmanager
from threading import Lock

import clickhouse_connect
from fastapi import HTTPException

from app.config import settings


class ClickHouseRepository:
    """Lazy shared HTTP pool with session IDs disabled for concurrent reads."""

    def __init__(self):
        self.client = None
        self.lock = Lock()

    @contextmanager
    def connection(self):
        if not settings.clickhouse_host or not settings.clickhouse_user:
            raise HTTPException(503, "Заполните CLICKHOUSE_HOST и CLICKHOUSE_USER в .env и пересоздайте контейнеры.")
        try:
            with self.lock:
                if self.client is None:
                    self.client = clickhouse_connect.get_client(
                        host=settings.clickhouse_host, port=settings.clickhouse_http_port,
                        username=settings.clickhouse_user, password=settings.clickhouse_password,
                        database=settings.clickhouse_database, secure=settings.clickhouse_secure,
                        verify=settings.clickhouse_verify,
                        connect_timeout=settings.clickhouse_connect_timeout,
                        send_receive_timeout=settings.clickhouse_query_timeout + 5,
                        autogenerate_session_id=False,
                    )
                client = self.client
            yield client
        except HTTPException:
            raise
        except Exception:
            # Driver exceptions can contain credentials and server addresses.
            raise HTTPException(503, "Ошибка чтения ClickHouse. Проверьте сеть из Docker, TLS, read-only пользователя и таймауты.") from None

    def select(self, sql, parameters=None):
        with self.connection() as client:
            result = client.query(sql, parameters=parameters or {}, settings={
                "max_execution_time": settings.clickhouse_query_timeout,
            })
            return list(result.named_results())

    def close(self):
        with self.lock:
            if self.client:
                self.client.close()
                self.client = None


repository = ClickHouseRepository()
