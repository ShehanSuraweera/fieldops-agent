"""FSM: assets, service history, fault catalog, technician calendars and work orders."""

import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import any_, func, or_, select
from sqlalchemy.orm import Session

from app.config import LK_TZ, now_lk, to_lk
from app.db import get_db
from app.errors import APIError, invalid_reference, not_found
from app.models import (
    Asset,
    Availability,
    FaultCode,
    ServiceHistory,
    Technician,
    Ticket,
    WorkOrder,
    work_order_id_seq,
)
from app.schemas import (
    AssetOut,
    FaultCodeOut,
    ServiceRecordOut,
    SlotOut,
    TechnicianAvailabilityOut,
    WorkOrderCreate,
    WorkOrderOut,
)

router = APIRouter()

SLOT_HOURS = 2
DEFAULT_WINDOW_DAYS = 14


@router.get("/assets", response_model=list[AssetOut])
def list_assets(
    customer_id: str | None = None,
    q: str | None = Query(
        None, description="Every word must appear in the asset id, name, model or site (case-insensitive)"
    ),
    db: Session = Depends(get_db),
) -> list[Asset]:
    stmt = select(Asset).order_by(Asset.id)
    if customer_id:
        stmt = stmt.where(Asset.customer_id == customer_id)
    for word in (q or "").split():
        pattern = f"%{word}%"
        stmt = stmt.where(
            or_(
                Asset.id.ilike(pattern),
                Asset.name.ilike(pattern),
                Asset.model.ilike(pattern),
                Asset.site.ilike(pattern),
            )
        )
    return list(db.scalars(stmt))


def _get_asset(db: Session, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise not_found("asset", asset_id)
    return asset


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: str, db: Session = Depends(get_db)) -> Asset:
    return _get_asset(db, asset_id)


@router.get("/assets/{asset_id}/history", response_model=list[ServiceRecordOut])
def get_asset_history(asset_id: str, db: Session = Depends(get_db)) -> list[ServiceHistory]:
    """Service history for one asset, newest first."""
    _get_asset(db, asset_id)
    stmt = (
        select(ServiceHistory)
        .where(ServiceHistory.asset_id == asset_id)
        .order_by(ServiceHistory.date.desc(), ServiceHistory.id.desc())
    )
    return list(db.scalars(stmt))


@router.get("/fault-codes", response_model=list[FaultCodeOut])
def list_fault_codes(
    model: str | None = Query(None, description="Only fault codes that apply to this asset model"),
    db: Session = Depends(get_db),
) -> list[FaultCode]:
    stmt = select(FaultCode).order_by(FaultCode.code)
    if model:
        stmt = stmt.where(model == any_(FaultCode.applies_to_models))
    return list(db.scalars(stmt))


def _slot_bounds(slot: Availability) -> tuple[datetime, datetime]:
    start = datetime.combine(slot.date, slot.start, tzinfo=LK_TZ)
    end = datetime.combine(slot.date, slot.end, tzinfo=LK_TZ)
    return start, end


@router.get("/technicians/available", response_model=list[TechnicianAvailabilityOut])
def list_available_technicians(
    region: str | None = None,
    skill: str | None = None,
    from_: datetime | None = Query(None, alias="from", description="Default: now. Naive = Colombo time"),
    to: datetime | None = Query(None, description=f"Default: from + {DEFAULT_WINDOW_DAYS} days"),
    db: Session = Depends(get_db),
) -> list[TechnicianAvailabilityOut]:
    """Open slots per technician that fit entirely inside [from, to]."""
    window_start = to_lk(from_) if from_ else now_lk()
    window_end = to_lk(to) if to else window_start + timedelta(days=DEFAULT_WINDOW_DAYS)
    if window_end <= window_start:
        raise APIError(422, "invalid_window", "'to' must be after 'from'")

    tech_stmt = select(Technician).order_by(Technician.id)
    if region:
        tech_stmt = tech_stmt.where(func.lower(Technician.region) == region.lower())
    if skill:
        tech_stmt = tech_stmt.where(skill.lower() == any_(Technician.skills))
    technicians = list(db.scalars(tech_stmt))
    if not technicians:
        return []

    tech_ids = [t.id for t in technicians]
    date_range = (window_start.date(), window_end.date())
    slots = db.scalars(
        select(Availability)
        .where(Availability.technician_id.in_(tech_ids), Availability.date.between(*date_range))
        .order_by(Availability.date, Availability.start)
    ).all()

    jobs: dict[tuple[str, object], int] = {}
    for slot in slots:
        if slot.booked:
            key = (slot.technician_id, slot.date)
            jobs[key] = jobs.get(key, 0) + 1

    open_slots: dict[str, list[SlotOut]] = {tech_id: [] for tech_id in tech_ids}
    for slot in slots:
        start, end = _slot_bounds(slot)
        if slot.booked or start < window_start or end > window_end:
            continue
        open_slots[slot.technician_id].append(
            SlotOut(
                slot_id=slot.id,
                start=start,
                end=end,
                jobs_that_day=jobs.get((slot.technician_id, slot.date), 0),
            )
        )

    return [
        TechnicianAvailabilityOut(
            technician_id=t.id, name=t.name, region=t.region, skills=t.skills, slots=open_slots[t.id]
        )
        for t in technicians
        if open_slots[t.id]
    ]


@router.post("/work-orders", response_model=WorkOrderOut, status_code=201)
def create_work_order(payload: WorkOrderCreate, db: Session = Depends(get_db)) -> WorkOrder:
    """Book a technician. The visit takes ceil(est_hours / 2) consecutive open slots on one day."""
    if db.get(Ticket, payload.ticket_id) is None:
        raise invalid_reference("ticket", payload.ticket_id)
    if db.get(Asset, payload.asset_id) is None:
        raise invalid_reference("asset", payload.asset_id)
    if db.get(Technician, payload.technician_id) is None:
        raise invalid_reference("technician", payload.technician_id)

    start = to_lk(payload.scheduled_start)
    slot_count = math.ceil(payload.est_hours / SLOT_HOURS)
    starts = [start + timedelta(hours=SLOT_HOURS * i) for i in range(slot_count)]
    wanted = {s.time() for s in starts}

    slots = db.scalars(
        select(Availability)
        .where(
            Availability.technician_id == payload.technician_id,
            Availability.date == start.date(),
            Availability.start.in_(wanted),
        )
        .with_for_update()
    ).all()
    same_day = all(s.date() == start.date() for s in starts)
    if not same_day or len(slots) != slot_count or any(slot.booked for slot in slots):
        db.rollback()
        raise APIError(
            409,
            "slot_unavailable",
            f"Technician '{payload.technician_id}' has no {slot_count} consecutive open slot(s) "
            f"starting {start.isoformat()}",
            {
                "technician_id": payload.technician_id,
                "scheduled_start": start.isoformat(),
                "slots_needed": slot_count,
            },
        )

    for slot in slots:
        slot.booked = True
    seq = db.scalar(select(work_order_id_seq.next_value()))
    work_order = WorkOrder(
        id=f"WO-{seq:06d}",
        ticket_id=payload.ticket_id,
        asset_id=payload.asset_id,
        technician_id=payload.technician_id,
        scheduled_start=start,
        est_hours=payload.est_hours,
        status="scheduled",
    )
    db.add(work_order)
    db.commit()
    db.refresh(work_order)
    return work_order


@router.get("/work-orders/{work_order_id}", response_model=WorkOrderOut)
def get_work_order(work_order_id: str, db: Session = Depends(get_db)) -> WorkOrder:
    work_order = db.get(WorkOrder, work_order_id)
    if work_order is None:
        raise not_found("work order", work_order_id)
    return work_order
