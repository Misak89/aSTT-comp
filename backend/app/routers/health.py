from fastapi import APIRouter
from datetime import datetime, timezone

router = APIRouter()

_STARTED_AT = datetime.now(timezone.utc).isoformat()


@router.get("/api/health")
def health():
    ram = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        ram = {
            "ram_total_mb": round(vm.total / 1024**2),
            "ram_used_mb": round(vm.used / 1024**2),
            "ram_percent": vm.percent,
        }
    except Exception:
        pass
    return {"status": "ok", "utc": datetime.now(timezone.utc).isoformat(), "started_at": _STARTED_AT, **ram}
