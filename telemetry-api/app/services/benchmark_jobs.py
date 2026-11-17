"""Single-process, file-backed local job queue. No external broker or background worker."""
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal
import csv
import io
import json
import math
import os
import secrets
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

Variant = Literal["superset", "infinity", "direct"]
ErrorCode = Literal[
    "network_error", "query_error", "timeout", "embed_error", "missing_panels",
    "invalid_data", "row_count", "different_data", "filtered_query", "unsupported_query",
    "render_error", "render_unobservable", "runner_lost", "preflight_failed", "internal_error",
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class JobRequest(Model):
    variants: list[Variant] = Field(default_factory=lambda: ["superset", "infinity", "direct"], min_length=1, max_length=3)
    repetitions: int = Field(1, ge=1, le=50)
    cache: Literal["cold", "warm", "both"] = "cold"
    timeout_seconds: int = Field(120, ge=10, le=300)

    @model_validator(mode="after")
    def unique_variants(self):
        if len(set(self.variants)) != len(self.variants):
            raise ValueError("variants must be unique")
        return self


class PanelResult(Model):
    rows: int = Field(ge=0, le=200000)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    data_ms: float = Field(ge=0, le=3600000)
    ready_ms: float | None = Field(None, ge=0, le=3600000)
    response_bytes: int = Field(0, ge=0, le=100_000_000)
    cache_hit: bool | None = None
    renderer: Literal["table", "timeseries", "echarts_timeseries_bar", "echarts_timeseries_line", "unknown"] = "unknown"


class RunResult(Model):
    variant: Variant
    phase: Literal["preflight", "warmup", "measured"]
    cache: Literal["cold", "warm"]
    repetition: int = Field(0, ge=0, le=50)
    started_at: datetime
    success: bool
    errors: list[ErrorCode] = Field(default_factory=list, max_length=10)
    iframe_ms: float | None = Field(None, ge=0, le=3600000)
    data_ms: float | None = Field(None, ge=0, le=3600000)
    ready_ms: float | None = Field(None, ge=0, le=3600000)
    panels: dict[Literal["hourly", "raw"], PanelResult] = Field(default_factory=dict, max_length=2)
    readiness: Literal["dom_observed_approximate"] = "dom_observed_approximate"
    sql_ms: None = None

    @model_validator(mode="after")
    def successful_has_data(self):
        if self.started_at.tzinfo is None:
            raise ValueError("started_at requires timezone")
        if self.success and (self.errors or set(self.panels) != {"hourly", "raw"}
                             or self.panels["raw"].rows != 100000 or not self.panels["hourly"].rows
                             or self.ready_ms is None or self.data_ms is None
                             or any(p.ready_ms is None for p in self.panels.values())):
            raise ValueError("Successful run requires both observed panels and 100000 raw records")
        if self.success and (self.data_ms != max(p.data_ms for p in self.panels.values())
                             or self.ready_ms != max(p.ready_ms for p in self.panels.values())
                             or any(p.ready_ms < p.data_ms for p in self.panels.values())):
            raise ValueError("Readiness must follow data completion for each panel")
        return self


class RunnerRequest(Model):
    runner_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class Progress(RunnerRequest):
    lease: str = Field(pattern=r"^[a-f0-9]{64}$")
    run: RunResult


class Finish(RunnerRequest):
    lease: str = Field(pattern=r"^[a-f0-9]{64}$")
    error: ErrorCode | None = None
    browser_version: str = Field("unknown", pattern=r"^[0-9.a-zA-Z -]{1,80}$")
    playwright_version: str = Field("unknown", pattern=r"^[0-9.a-zA-Z -]{1,40}$")
    headless: bool = True


def now():
    return datetime.now(timezone.utc).isoformat()


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    pos = (len(values) - 1) * fraction
    lo = math.floor(pos)
    return round(values[lo] + (values[math.ceil(pos)] - values[lo]) * (pos - lo), 3)


def summary(runs):
    result = []
    for variant in ("superset", "infinity", "direct"):
        for cache in ("cold", "warm"):
            group = [r for r in runs if r["variant"] == variant and r["cache"] == cache and r["phase"] == "measured"]
            if not group:
                continue
            row = {"variant": variant, "cache": cache, "runs": len(group), "errors": sum(not r["success"] for r in group)}
            for metric in ("iframe_ms", "data_ms", "ready_ms"):
                values = [r[metric] for r in group if r["success"] and r.get(metric) is not None]
                row[metric] = {"min": percentile(values, 0), "p50": percentile(values, .5),
                               "p95": percentile(values, .95), "max": percentile(values, 1)}
            result.append(row)
    return result


class JobStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = RLock()

    def read(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "state.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"jobs": [], "runner": None}
        stamp = time.time()
        for job in state["jobs"]:
            stale_running = job["status"] == "running" and (
                stamp - job.get("heartbeat", 0) > 60 or
                stamp - job.get("progress_timestamp", job.get("heartbeat", 0)) > job["request"]["timeout_seconds"] + 60)
            stale_pending = job["status"] == "pending" and stamp - job["created_timestamp"] > 3600
            if stale_running or stale_pending:
                job.update(status="failed", error="runner_lost", finished_at=now())
                job.pop("lease", None)
        return state

    def write(self, state):
        # 20 jobs x at most 606 compact results; no response bodies, credentials or traces.
        text = json.dumps(state, ensure_ascii=False, allow_nan=False)
        if len(text.encode("utf-8")) > 32_000_000:
            raise HTTPException(413, "Превышен общий размер результатов (32 MB).")
        temporary = self.directory / "state.tmp"
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, self.directory / "state.json")

    def public(self, job, full=True):
        result = {k: v for k, v in job.items() if k not in {"lease", "runner_id", "heartbeat", "progress_timestamp", "created_timestamp", "runs"}}
        result["completed_runs"] = len(job["runs"])
        if full:
            result["runs"] = job["runs"]
            result["summary"] = summary(job["runs"])
        return result

    def list(self):
        with self.lock:
            state = self.read()
            self.write(state)
            runner = state["runner"]
            return {"runner_online": bool(runner and time.time() - runner["seen"] < 15),
                    "jobs": [self.public(j, False) for j in reversed(state["jobs"])]}

    def create(self, request):
        with self.lock:
            state = self.read()
            if any(j["status"] in ("pending", "running") for j in state["jobs"]):
                raise HTTPException(409, "Уже есть pending/running задание. Дождитесь завершения или отмените его.")
            job = {"id": uuid4().hex, "status": "pending", "created_at": now(), "created_timestamp": time.time(),
                   "request": request.model_dump(), "runs": [], "error": None,
                   "readiness": "dom_observed_approximate", "sql_time": "unavailable"}
            state["jobs"] = state["jobs"][-19:] + [job]
            self.write(state)
            return self.public(job)

    def find(self, state, job_id):
        job = next((j for j in state["jobs"] if j["id"] == job_id), None)
        if job is None:
            raise HTTPException(404, "Задание не найдено.")
        return job

    def get(self, job_id):
        with self.lock:
            state = self.read()
            job = self.find(state, job_id)
            self.write(state)
            return self.public(job)

    def heartbeat(self, runner_id):
        with self.lock:
            state = self.read()
            owner = state["runner"]
            if owner and owner["id"] != runner_id and time.time() - owner["seen"] < 60:
                raise HTTPException(409, "Другой runner уже зарегистрирован.")
            state["runner"] = {"id": runner_id, "seen": time.time()}
            for job in state["jobs"]:
                if job["status"] == "running" and job.get("runner_id") == runner_id:
                    job["heartbeat"] = time.time()
            self.write(state)
            return {"status": "ok"}

    def claim(self, runner_id):
        with self.lock:
            self.heartbeat(runner_id)
            state = self.read()
            if any(j["status"] == "running" for j in state["jobs"]):
                return None
            job = next((j for j in state["jobs"] if j["status"] == "pending"), None)
            if job is None:
                return None
            job.update(status="running", runner_id=runner_id, lease=secrets.token_hex(32),
                       heartbeat=time.time(), progress_timestamp=time.time(), started_at=now())
            self.write(state)
            return {**self.public(job), "lease": job["lease"]}

    def release(self, runner_id):
        with self.lock:
            state = self.read()
            if state["runner"] and state["runner"]["id"] == runner_id:
                state["runner"] = None
                for job in state["jobs"]:
                    if job["status"] == "running" and job.get("runner_id") == runner_id:
                        job.update(status="failed", error="runner_lost", finished_at=now())
                        job.pop("lease", None)
                self.write(state)
            return {"status": "ok"}

    def owned(self, state, job_id, request):
        job = self.find(state, job_id)
        if job["status"] != "running" or job.get("runner_id") != request.runner_id or not secrets.compare_digest(job.get("lease", ""), request.lease):
            raise HTTPException(409, "Задание больше не принадлежит этому runner.")
        return job

    def progress(self, job_id, request):
        with self.lock:
            state = self.read()
            job = self.owned(state, job_id, request)
            run = request.run
            if any((r["variant"], r["phase"], r["cache"], r["repetition"]) == (run.variant, run.phase, run.cache, run.repetition) for r in job["runs"]):
                raise HTTPException(409, "Результат этого запуска уже сохранён.")
            if run.phase == "preflight":
                if run.cache != "cold" or run.repetition != 0:
                    raise HTTPException(422, "Preflight требует cold и repetition=0.")
            else:
                preflight = [r for r in job["runs"] if r["phase"] == "preflight"]
                if len(preflight) != 3 or not all(r["success"] for r in preflight):
                    raise HTTPException(409, "Сначала требуется успешный preflight всех трёх вариантов.")
                if run.variant not in job["request"]["variants"] or (job["request"]["cache"] != "both" and run.cache != job["request"]["cache"]):
                    raise HTTPException(422, "Вариант/режим не соответствует заданию.")
                if run.phase == "measured" and not 1 <= run.repetition <= job["request"]["repetitions"]:
                    raise HTTPException(422, "Неверный номер повтора.")
                if run.phase == "warmup" and (run.cache != "warm" or run.repetition != 0):
                    raise HTTPException(422, "Неверный warmup.")
            limit = 3 + 2 * len(job["request"]["variants"]) * (job["request"]["repetitions"] + 1)
            if len(job["runs"]) >= limit:
                raise HTTPException(413, "Превышено число результатов задания.")
            job["runs"].append(request.run.model_dump(mode="json"))
            job["heartbeat"] = time.time()
            job["progress_timestamp"] = time.time()
            self.write(state)
            return {"status": "ok"}

    def finish(self, job_id, request):
        with self.lock:
            state = self.read()
            job = self.owned(state, job_id, request)
            measured = [r for r in job["runs"] if r["phase"] == "measured"]
            expected = len(job["request"]["variants"]) * job["request"]["repetitions"] * (2 if job["request"]["cache"] == "both" else 1)
            preflight = [r for r in job["runs"] if r["phase"] == "preflight"]
            hashes = {(r["panels"]["hourly"]["fingerprint"], r["panels"]["raw"]["fingerprint"]) for r in preflight if r["success"]}
            valid_preflight = len(preflight) == 3 and {r["variant"] for r in preflight} == {"superset", "infinity", "direct"} and all(r["success"] for r in preflight) and len(hashes) == 1
            error = request.error or ("preflight_failed" if not valid_preflight else None)
            for run in measured:
                signatures = {(run["panels"]["hourly"]["fingerprint"], run["panels"]["raw"]["fingerprint"])} if set(run["panels"]) == {"hourly", "raw"} else set()
                if run["success"] and (not valid_preflight or signatures != hashes):
                    run.update(success=False, errors=["different_data"], ready_ms=None)
                    error = error or "different_data"
            if not error and (len(measured) != expected or any(not r["success"] for r in measured)):
                error = "query_error"
            job.update(status="failed" if error else "completed", error=error,
                       finished_at=now(), browser_version=request.browser_version,
                       viewport={"width": 1600, "height": 1100}, server_cache="uncontrolled")
            job["runner_environment"] = {"playwright": request.playwright_version, "headless": request.headless}
            job["browser_timezone"] = "Europe/Moscow"
            job.pop("lease", None)
            self.write(state)
            return self.public(job)

    def cancel(self, job_id):
        with self.lock:
            state = self.read()
            job = self.find(state, job_id)
            if job["status"] in ("pending", "running"):
                job.update(status="failed", error="runner_lost", finished_at=now())
                job.pop("lease", None)
            self.write(state)
            return self.public(job)


def csv_report(job):
    stream = io.StringIO(newline="")
    fields = ["variant", "phase", "cache", "repetition", "started_at", "success", "iframe_ms", "data_ms", "ready_ms", "readiness", "errors", "hourly_rows", "raw_rows", "response_bytes", "sql_ms"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for run in job["runs"]:
        panels = run["panels"]
        row = {k: run.get(k) for k in fields if k in run}
        row.update(errors=";".join(run["errors"]), hourly_rows=panels.get("hourly", {}).get("rows"),
                   raw_rows=panels.get("raw", {}).get("rows"), response_bytes=sum(p["response_bytes"] for p in panels.values()))
        writer.writerow(row)
    return stream.getvalue()
