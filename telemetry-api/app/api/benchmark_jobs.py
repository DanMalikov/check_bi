import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.services.benchmark_jobs import JobStore, JobRequest, RunnerRequest, Progress, Finish, csv_report

store = JobStore(os.getenv("BENCHMARK_RESULTS_DIR", "/app/benchmark_results"))


async def local_mutation(request: Request):
    if request.method == "POST":
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "Требуется application/json.")
        origin = request.headers.get("origin")
        if origin and origin != os.getenv("ADMIN_ORIGIN", "http://localhost:5173"):
            raise HTTPException(403, "Разрешён только origin локальной Admin.")
        if int(request.headers.get("content-length", "0")) > 32768:
            raise HTTPException(413, "Слишком большой запрос.")
        body = await request.body()
        if len(body) > 32768:
            raise HTTPException(413, "Слишком большой запрос.")


router = APIRouter(prefix="/api/benchmark/jobs", tags=["Local benchmark jobs"], dependencies=[Depends(local_mutation)])


@router.get("")
def jobs(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return store.list()


@router.post("")
def create(request: JobRequest):
    return store.create(request)


@router.post("/runner/heartbeat")
def heartbeat(request: RunnerRequest):
    return store.heartbeat(request.runner_id)


@router.post("/runner/claim")
def claim(request: RunnerRequest):
    return store.claim(request.runner_id)


@router.post("/runner/release")
def release(request: RunnerRequest):
    return store.release(request.runner_id)


@router.get("/{job_id}")
def job(job_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return store.get(job_id)


@router.get("/{job_id}/export.json")
def export_json(job_id: str, response: Response):
    job = store.get(job_id)
    response.headers["Content-Disposition"] = f'attachment; filename="benchmark-{job["id"]}.json"'
    return job


@router.get("/{job_id}/export.csv")
def export_csv(job_id: str):
    job = store.get(job_id)
    return Response(csv_report(job), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="benchmark-{job["id"]}.csv"'})


@router.post("/{job_id}/progress")
def progress(job_id: str, request: Progress):
    return store.progress(job_id, request)


@router.post("/{job_id}/finish")
def finish(job_id: str, request: Finish):
    return store.finish(job_id, request)


@router.post("/{job_id}/cancel")
def cancel(job_id: str):
    return store.cancel(job_id)
