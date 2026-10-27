"""Read-only notification diagnostics for release managers."""
from fastapi import APIRouter, Depends

from app.deps import require_roles
from app.services.notification_service import poller

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/status")
def notification_status(user=Depends(require_roles("RM"))):
    return poller.status()
