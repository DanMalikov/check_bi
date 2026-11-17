"""python -m benchmark.runner [--once] : one local runner, sequential browser tests."""
import argparse
import asyncio
from pathlib import Path
import json
import os
import re
from uuid import uuid4
from importlib.metadata import version

import httpx
from playwright.async_api import async_playwright

from .browser import BrowserMeasurement, CONTEXT_OPTIONS
from .config import defaults


async def run_job(client, browser, admin_url, job, runner_id, output, headless=True):
    job_id = job["id"]
    ownership = {"runner_id": runner_id, "lease": job["lease"]}
    request = job["request"]
    contexts = {}
    baselines = None
    results = []
    error = None

    async def measure(variant, cache, phase, index=0):
        if cache == "warm":
            if variant not in contexts:
                contexts[variant] = await browser.new_context(**CONTEXT_OPTIONS)
            context = contexts[variant]
        else:
            context = await browser.new_context(**CONTEXT_OPTIONS)
        result = await BrowserMeasurement(context, admin_url, variant, request["timeout_seconds"], phase, cache, index).run()
        if cache == "cold":
            await context.close()
        signatures = {(r, p["fingerprint"]) for r, p in result["panels"].items()}
        if result["success"] and baselines is not None and signatures != baselines:
            result.update(success=False, errors=["different_data"], ready_ms=None)
        response = await client.post(f"/api/benchmark/jobs/{job_id}/progress", json={**ownership, "run": result})
        response.raise_for_status()
        results.append(result)
        print(f'{phase}: {variant}/{cache} #{index}: {"ok" if result["success"] else ",".join(result["errors"])}', flush=True)
        return result

    try:
        # Data validation is separate from timings. Fresh contexts; this DOES warm server caches.
        order = ["superset", "infinity", "direct"]
        offset = int(job_id[-2:], 16) % 3
        order = order[offset:] + order[:offset]
        for variant in order:
            result = await measure(variant, "cold", "preflight")
            if not result["success"]:
                error = "preflight_failed"
                break
            if baselines is None:
                baselines = {(r, p["fingerprint"]) for r, p in result["panels"].items()}
        if error is None:
            variants = request["variants"]
            warmed = set()
            modes = ["cold", "warm"] if request["cache"] == "both" else [request["cache"]]
            for cache in modes:
                for index in range(request["repetitions"]):
                    shift = (offset + index) % len(variants)
                    for variant in variants[shift:] + variants[:shift]:
                        if cache == "warm" and variant not in warmed:
                            result = await measure(variant, cache, "warmup")
                            if not result["success"]:
                                error = "query_error"
                                break
                            warmed.add(variant)
                        result = await measure(variant, cache, "measured", index + 1)
                        if not result["success"]:
                            # Stop on failure: a timed-out backend request might still be running.
                            error = result["errors"][0] if result["errors"] else "query_error"
                            break
                    if error:
                        break
                if error:
                    break
    except Exception:
        # Do not serialize exception text: BI errors can contain URLs, credentials or tokens.
        error = "internal_error"
    finally:
        for context in contexts.values():
            await context.close()
    response = await client.post(f"/api/benchmark/jobs/{job_id}/finish", json={**ownership, "error": error, "browser_version": browser.version,
        "playwright_version": version("playwright"), "headless": headless})
    if response.status_code == 409:
        print("Job no longer owned (cancelled or lease expired); no timings published.", flush=True)
        return
    response.raise_for_status()
    report = response.json()
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{job_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    csv = await client.get(f"/api/benchmark/jobs/{job_id}/export.csv")
    csv.raise_for_status()
    (output / f"{job_id}.csv").write_text(csv.text, encoding="utf-8-sig")
    reports = sorted((p for p in output.glob("*.json") if re.fullmatch(r"[a-f0-9]{32}\.json", p.name)), key=lambda p: p.stat().st_mtime)
    for old in reports[:-20]:
        old.unlink()
        old.with_suffix(".csv").unlink(missing_ok=True)
    print("Job", job_id, report["status"], report.get("error") or "", flush=True)


async def main(args):
    runner_id = uuid4().hex
    async with httpx.AsyncClient(base_url=args.api.rstrip("/"), timeout=20, trust_env=False) as client:
        alive = True

        async def heartbeat():
            while alive:
                response = await client.post("/api/benchmark/jobs/runner/heartbeat", json={"runner_id": runner_id})
                response.raise_for_status()
                await asyncio.sleep(3)

        response = await client.post("/api/benchmark/jobs/runner/heartbeat", json={"runner_id": runner_id})
        response.raise_for_status()
        heart = asyncio.create_task(heartbeat())
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=not args.headed)
                print("Runner online; create a job in Admin. Ctrl+C to stop.", flush=True)
                while True:
                    if heart.done():
                        await heart
                    response = await client.post("/api/benchmark/jobs/runner/claim", json={"runner_id": runner_id})
                    response.raise_for_status()
                    job = response.json()
                    if job:
                        task = asyncio.create_task(run_job(client, browser, args.admin, job, runner_id, Path(args.output), not args.headed))
                        done, _ = await asyncio.wait({task, heart}, return_when=asyncio.FIRST_COMPLETED)
                        if heart in done:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                            await heart
                        await task
                        if args.once:
                            break
                    await asyncio.sleep(2)
                await browser.close()
        finally:
            alive = False
            heart.cancel()
            await asyncio.gather(heart, return_exceptions=True)
            try:
                await client.post("/api/benchmark/jobs/runner/release", json={"runner_id": runner_id})
            except Exception:
                pass  # Abrupt loss is handled by the server's lease timeout.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local sequential Telemetry Benchmark runner")
    urls = defaults()
    parser.add_argument("--api", default=urls["api"])
    parser.add_argument("--admin", default=urls["admin"])
    parser.add_argument("--output", default="benchmark-results")
    parser.add_argument("--once", action="store_true", help="Wait for one job, execute it, exit")
    parser.add_argument("--headed", action="store_true", help="Show Chromium for diagnostics; do not mix headed/headless results")
    args = parser.parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("Runner stopped. A running lease expires in 60 seconds.")
    except Exception as exc:
        print("Runner stopped:", type(exc).__name__, "(check API reachability, singleton runner and Chromium installation)")
        raise SystemExit(1)
