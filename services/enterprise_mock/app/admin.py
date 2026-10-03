"""Admin operations for test harnesses. Disabled unless MOCK_ADMIN_ENABLED=true."""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import APIError
from app.seed import resolve_today, seed

router = APIRouter()


class ReseedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    today: date | None = Field(None, description="Reference date; default SEED_TODAY or today in Colombo")


class ReseedResult(BaseModel):
    seeded_for: date


@router.post("/reseed", response_model=ReseedResult)
def reseed(payload: ReseedRequest, db: Session = Depends(get_db)) -> ReseedResult:
    """Wipe every CRM/FSM/ERP table and load the deterministic seed. Used by the eval runner."""
    if not settings.admin_enabled:
        raise APIError(403, "admin_disabled", "Admin endpoints are disabled (set MOCK_ADMIN_ENABLED=true)")
    today = payload.today or resolve_today()
    seed(db, today)
    return ReseedResult(seeded_for=today)
