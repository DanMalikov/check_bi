from datetime import datetime, timezone
import asyncio
from unittest.mock import patch

from fastapi import HTTPException
import httpx
import pytest

from app.main import app
from app.services.benchmark_jobs import JobStore, JobRequest, RunResult, Progress, Finish, summary, percentile


def valid_run(variant="superset", phase="preflight"):
    return RunResult(variant=variant, phase=phase, cache="cold", started_at=datetime.now(timezone.utc),
                     repetition=1 if phase == "measured" else 0,
                     success=True, iframe_ms=10, data_ms=100, ready_ms=150,
                     panels={"hourly": {"rows": 2, "fingerprint": "a" * 64, "data_ms": 50, "ready_ms": 100},
                             "raw": {"rows": 100000, "fingerprint": "b" * 64, "data_ms": 100, "ready_ms": 150}})


def test_single_job_runner_and_persistence(tmp_path):
    store = JobStore(tmp_path)
    job = store.create(JobRequest())
    with pytest.raises(HTTPException) as error:
        store.create(JobRequest())
    assert error.value.status_code == 409
    claim = store.claim("a" * 32)
    assert claim["id"] == job["id"]
    assert "lease" not in store.get(job["id"])
    assert store.claim("a" * 32) is None
    with pytest.raises(HTTPException):
        store.claim("b" * 32)
    assert JobStore(tmp_path).get(job["id"])["status"] == "running"


def test_stale_runner_fails_job(tmp_path):
    store = JobStore(tmp_path)
    job = store.create(JobRequest())
    store.claim("a" * 32)
    import time
    with patch("app.services.benchmark_jobs.time.time", return_value=time.time() + 61):
        result = store.get(job["id"])
    assert result["status"] == "failed" and result["error"] == "runner_lost"


def test_measured_result_cannot_precede_preflight_and_release_clears_runner(tmp_path):
    store = JobStore(tmp_path)
    job = store.create(JobRequest(variants=["superset"]))
    claim = store.claim("a" * 32)
    owner = {"runner_id": "a" * 32, "lease": claim["lease"]}
    with pytest.raises(HTTPException) as error:
        store.progress(job["id"], Progress(**owner, run=valid_run(phase="measured")))
    assert error.value.status_code == 409
    store.release("a" * 32)
    assert not store.list()["runner_online"]
    assert store.get(job["id"])["status"] == "failed"


def test_retention_is_bounded_and_result_replay_rejected(tmp_path):
    store = JobStore(tmp_path)
    for _ in range(21):
        job = store.create(JobRequest())
        store.cancel(job["id"])
    assert len(store.list()["jobs"]) == 20
    job = store.create(JobRequest())
    claim = store.claim("a" * 32)
    request = Progress(runner_id="a" * 32, lease=claim["lease"], run=valid_run())
    store.progress(job["id"], request)
    with pytest.raises(HTTPException):
        store.progress(job["id"], request)


def test_completion_requires_comparable_preflight_and_excludes_failures(tmp_path):
    store = JobStore(tmp_path)
    job = store.create(JobRequest(variants=["superset"]))
    claim = store.claim("a" * 32)
    owner = {"runner_id": "a" * 32, "lease": claim["lease"]}
    for variant in ("superset", "infinity", "direct"):
        store.progress(job["id"], Progress(**owner, run=valid_run(variant)))
    store.progress(job["id"], Progress(**owner, run=valid_run(phase="measured")))
    result = store.finish(job["id"], Finish(**owner))
    assert result["status"] == "completed"
    assert result["summary"][0]["ready_ms"]["p50"] == 150
    assert "lease" not in result and "runner_id" not in result


def test_percentiles_and_failed_runs():
    runs = [valid_run(phase="measured").model_dump(mode="json") for _ in range(3)]
    runs[0]["ready_ms"] = 100
    runs[1]["ready_ms"] = 200
    runs[2].update(success=False, ready_ms=9999)
    stats = summary(runs)[0]
    assert stats["errors"] == 1 and stats["runs"] == 3
    assert stats["ready_ms"] == {"min": 100, "p50": 150, "p95": 195, "max": 200}
    assert percentile([], .5) is None


def test_reject_invalid_success_and_unknown_report_fields():
    with pytest.raises(ValueError):
        RunResult(variant="superset", phase="measured", cache="cold", started_at=datetime.now(timezone.utc), success=True)
    with pytest.raises(ValueError):
        JobRequest(repetitions=51)
    with pytest.raises(ValueError):
        JobRequest(variants=["direct", "direct"])
    with pytest.raises(ValueError):
        RunResult(**{**valid_run().model_dump(), "guestToken": "must-not-be-stored"})


def test_local_api_guards_origin_and_body(tmp_path, monkeypatch):
    from app.api import benchmark_jobs
    monkeypatch.setattr(benchmark_jobs, "store", JobStore(tmp_path))

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/api/benchmark/jobs", json={}, headers={"Origin": "https://foreign.invalid"})).status_code == 403
            assert (await client.post("/api/benchmark/jobs", content="{}")).status_code == 415
            assert (await client.post("/api/benchmark/jobs", content='"' + 'x' * 33000 + '"', headers={"Content-Type": "application/json"})).status_code == 413
            response = await client.post("/api/benchmark/jobs", json={"variants": ["direct"], "repetitions": 30})
            assert response.status_code == 200
            job_id = response.json()["id"]
            assert (await client.get(f"/api/benchmark/jobs/{job_id}/export.csv")).status_code == 200
            assert (await client.post(f"/api/benchmark/jobs/{job_id}/cancel", json={})).json()["status"] == "failed"
    asyncio.run(check())
