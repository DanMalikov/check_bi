FROM apache/superset:6.1.0
USER root
RUN uv pip install --python /app/.venv/bin/python clickhouse-connect==1.6.0
COPY superset_config.py /app/pythonpath/superset_config.py
COPY bootstrap.py /app/bootstrap.py
COPY configure_clickhouse.py /app/configure_clickhouse.py
COPY test_config.py /app/test_config.py
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh
USER superset
ENTRYPOINT ["/bin/sh", "/app/start.sh"]
