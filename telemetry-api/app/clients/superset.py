import httpx
from fastapi import HTTPException
from app.config import settings


async def guest_token(dashboard_id: str):
    try:
        async with httpx.AsyncClient(base_url=settings.superset_internal_url.rstrip("/"),
                                     timeout=settings.superset_api_timeout) as client:
            response = await client.post("/api/v1/security/login", json={
                "username": settings.superset_tech_user, "password": settings.superset_tech_password,
                "provider": "db", "refresh": False,
            })
            response.raise_for_status()
            headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
            csrf = await client.get("/api/v1/security/csrf_token/", headers=headers)
            csrf.raise_for_status()
            headers["X-CSRFToken"] = csrf.json()["result"]
            response = await client.post("/api/v1/security/guest_token/", headers=headers, json={
                "user": {"username": "benchmark_guest", "first_name": "Benchmark", "last_name": "Guest"},
                "resources": [{"type": "dashboard", "id": dashboard_id}], "rls": [],
            })
            response.raise_for_status()
            return response.json()["token"]
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(502, "Не удалось получить guest token Superset. Проверьте его health, техническую учётную запись, права и embedded UUID.") from None
