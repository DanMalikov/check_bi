from uuid import UUID
from fastapi import APIRouter, HTTPException, Response
from app.config import settings
from app.clients.superset import guest_token

router = APIRouter(prefix="/api/superset", tags=["Superset"])


@router.get("/embed")
async def embed(response: Response):
    response.headers["Cache-Control"] = "no-store"
    dashboard_id = settings.superset_embedded_dashboard_id
    if not dashboard_id:
        raise HTTPException(409, "Создайте dashboard в Superset, включите Embed dashboard и задайте SUPERSET_EMBEDDED_DASHBOARD_ID в .env.")
    try:
        UUID(dashboard_id)
    except ValueError:
        raise HTTPException(409, "SUPERSET_EMBEDDED_DASHBOARD_ID должен быть embedded UUID, а не числовым ID.") from None
    return {"id": dashboard_id, "supersetDomain": settings.superset_public_url,
            "guestToken": await guest_token(dashboard_id)}
