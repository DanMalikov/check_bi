"""Only read public runner URLs/ports from root .env; never load BI credentials."""
import os
from pathlib import Path


def defaults():
    allowed = {"ADMIN_PORT", "API_PORT", "BENCHMARK_API_URL", "BENCHMARK_ADMIN_URL"}
    values = {}
    path = Path(__file__).resolve().parent.parent / ".env"
    if path.exists():
        with path.open(encoding="utf-8-sig") as source:
            for line in source:
                name, sep, value = line.partition("=")
                if sep and name.strip() in allowed:
                    values[name.strip()] = value.strip().strip("\"'")
    for name in allowed:
        if name in os.environ:
            values[name] = os.environ[name]
    return {
        "api": values.get("BENCHMARK_API_URL") or "http://localhost:" + values.get("API_PORT", "8000"),
        "admin": values.get("BENCHMARK_ADMIN_URL") or "http://localhost:" + values.get("ADMIN_PORT", "5173"),
    }
