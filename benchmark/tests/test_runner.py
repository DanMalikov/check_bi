import asyncio
from datetime import datetime, timezone
import json

import httpx
import pytest

from benchmark.runner import run_job


@pytest.mark.parametrize("repetitions", [1, 10, 30])
def test_sequential_schedule_and_preflight(repetitions, tmp_path, monkeypatch):
    calls = []
    active = 0
    runs = []
    job = {"id": "a" * 32, "lease": "b" * 64, "request": {"variants": ["superset", "infinity", "direct"], "repetitions": repetitions, "cache": "both", "timeout_seconds": 10}}

    class Context:
        async def close(self):
            pass

    class Browser:
        version = "145.0"
        async def new_context(self, **kwargs):
            return Context()

    class Measurement:
        def __init__(self, context, admin, variant, timeout, phase, cache, index):
            self.variant, self.phase, self.cache, self.index = variant, phase, cache, index
        async def run(self):
            nonlocal active
            active += 1
            assert active == 1
            calls.append((self.variant, self.phase, self.cache, self.index))
            await asyncio.sleep(0)
            active -= 1
            return {"variant": self.variant, "phase": self.phase, "cache": self.cache, "repetition": self.index,
                    "started_at": datetime.now(timezone.utc).isoformat(), "success": True, "errors": [],
                    "iframe_ms": 1, "data_ms": 2, "ready_ms": 3, "readiness": "dom_observed_approximate", "sql_ms": None,
                    "panels": {r: {"rows": 100000 if r == "raw" else 2, "fingerprint": ("a" if r == "raw" else "b") * 64, "data_ms": 2, "ready_ms": 3, "response_bytes": 10} for r in ("hourly", "raw")}}

    def handler(request):
        if request.url.path.endswith("/progress"):
            runs.append(json.loads(request.content)["run"])
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path.endswith("/finish"):
            assert json.loads(request.content)["error"] is None
            return httpx.Response(200, json={"status": "completed", "runs": runs})
        return httpx.Response(200, text="variant,ready_ms\nsynthetic,3\n")

    monkeypatch.setattr("benchmark.runner.BrowserMeasurement", Measurement)
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://local") as client:
            await run_job(client, Browser(), "http://admin", job, "c" * 32, tmp_path)
    asyncio.run(check())
    assert len([c for c in calls if c[1] == "preflight"]) == 3
    assert len([c for c in calls if c[1] == "warmup"]) == 3
    assert len([c for c in calls if c[1] == "measured"]) == 6 * repetitions
    assert all(c[1] == "preflight" for c in calls[:3])
    assert len(list(tmp_path.glob("*.json"))) == 1 and len(list(tmp_path.glob("*.csv"))) == 1
