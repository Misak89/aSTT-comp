from fastapi import APIRouter
from datetime import datetime, timezone

router = APIRouter()


@router.get("/api/health")
def health():
    return {"status": "ok", "utc": datetime.now(timezone.utc).isoformat()}
