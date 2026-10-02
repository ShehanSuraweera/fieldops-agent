"""ERP: parts catalog and stock, reservations, vendors and purchase orders."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import any_, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import APIError, invalid_reference, not_found
from app.models import (
    Part,
    POLine,
    PurchaseOrder,
    Reservation,
    Ticket,
    Vendor,
    VendorPart,
    purchase_order_id_seq,
)
from app.schemas import (
    PartOut,
    PurchaseOrderCreate,
    PurchaseOrderOut,
    PurchaseOrderUpdate,
    ReservationCreate,
    ReservationOut,
    VendorOut,
)

router = APIRouter()

# Allowed purchase order status changes. Anything else is a 409.
PO_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending_approval", "approved"},
    "pending_approval": {"approved", "rejected"},
    "approved": {"sent"},
    "rejected": set(),
    "sent": set(),
}


@router.get("/parts", response_model=list[PartOut])
def list_parts(
    model: str | None = Query(None, description="Only parts compatible with this asset model"),
    q: str | None = Query(None, description="Substring of the sku or name"),
    db: Session = Depends(get_db),
) -> list[Part]:
    stmt = select(Part).order_by(Part.sku)
    if model:
        stmt = stmt.where(model == any_(Part.compatible_models))
    if q:
        stmt = stmt.where(or_(Part.sku.ilike(f"%{q}%"), Part.name.ilike(f"%{q}%")))
    return list(db.scalars(stmt))


@router.get("/parts/{sku}", response_model=PartOut)
def get_part(sku: str, db: Session = Depends(get_db)) -> Part:
    part = db.get(Part, sku)
    if part is None:
        raise not_found("part", sku)
    return part


@router.post("/reservations", response_model=ReservationOut, status_code=201)
def create_reservation(payload: ReservationCreate, db: Session = Depends(get_db)) -> ReservationOut:
    """Hold stock for a ticket. Stock is reduced immediately."""
    if db.get(Ticket, payload.ticket_id) is None:
        raise invalid_reference("ticket", payload.ticket_id)
    part = db.get(Part, payload.sku, with_for_update=True)
    if part is None:
        raise invalid_reference("part", payload.sku)
    if part.stock_qty < payload.qty:
        available = part.stock_qty
        db.rollback()
        raise APIError(
            409,
            "insufficient_stock",
            f"Only {available} of '{payload.sku}' in stock, {payload.qty} requested",
            {"sku": payload.sku, "available": available, "requested": payload.qty},
        )

    part.stock_qty -= payload.qty
    reservation = Reservation(sku=payload.sku, ticket_id=payload.ticket_id, qty=payload.qty)
    db.add(reservation)
    db.commit()
    return ReservationOut(
        id=reservation.id,
        sku=reservation.sku,
        ticket_id=reservation.ticket_id,
        qty=reservation.qty,
        stock_qty_after=part.stock_qty,
    )


@router.get("/vendors", response_model=list[VendorOut])
def list_vendors(
    sku: str | None = Query(None, description="Only vendors that supply this part, with their price"),
    db: Session = Depends(get_db),
) -> list[VendorOut]:
    if not sku:
        return [
            VendorOut.model_validate(v, from_attributes=True)
            for v in db.scalars(select(Vendor).order_by(Vendor.id))
        ]

    if db.get(Part, sku) is None:
        raise not_found("part", sku)
    rows = db.execute(
        select(Vendor, VendorPart.price_lkr)
        .join(VendorPart, VendorPart.vendor_id == Vendor.id)
        .where(VendorPart.sku == sku)
        .order_by(Vendor.id)
    ).all()
    return [
        VendorOut(id=v.id, name=v.name, approved=v.approved, lead_time_days=v.lead_time_days, price_lkr=price)
        for v, price in rows
    ]


def _get_po(db: Session, po_id: str) -> PurchaseOrder:
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise not_found("purchase order", po_id)
    return po


@router.post("/purchase-orders", response_model=PurchaseOrderOut, status_code=201)
def create_purchase_order(payload: PurchaseOrderCreate, db: Session = Depends(get_db)) -> PurchaseOrder:
    """Draft a PO. Unit prices and the total come from the vendor's price list."""
    vendor = db.get(Vendor, payload.vendor_id)
    if vendor is None:
        raise invalid_reference("vendor", payload.vendor_id)
    if not vendor.approved:
        raise APIError(
            422,
            "vendor_not_approved",
            f"Vendor '{vendor.id}' is not on the approved vendor list",
            {"vendor_id": vendor.id},
        )
    if payload.ticket_id is not None and db.get(Ticket, payload.ticket_id) is None:
        raise invalid_reference("ticket", payload.ticket_id)

    skus = [line.sku for line in payload.lines]
    if len(set(skus)) != len(skus):
        raise APIError(422, "duplicate_line", "Each sku may appear only once per purchase order")
    prices: dict[str, int] = {
        sku: price
        for sku, price in db.execute(
            select(VendorPart.sku, VendorPart.price_lkr).where(
                VendorPart.vendor_id == vendor.id, VendorPart.sku.in_(skus)
            )
        )
    }
    missing = [sku for sku in skus if sku not in prices]
    if missing:
        raise APIError(
            422,
            "sku_not_supplied",
            f"Vendor '{vendor.id}' does not supply: {', '.join(missing)}",
            {"vendor_id": vendor.id, "skus": missing},
        )

    seq = db.scalar(select(purchase_order_id_seq.next_value()))
    po = PurchaseOrder(
        id=f"PO-{seq:06d}",
        vendor_id=vendor.id,
        ticket_id=payload.ticket_id,
        status="draft",
        total_lkr=sum(prices[line.sku] * line.qty for line in payload.lines),
        warranty_claim=payload.warranty_claim,
        created_by=payload.created_by,
    )
    po.lines = [POLine(sku=line.sku, qty=line.qty, unit_price_lkr=prices[line.sku]) for line in payload.lines]
    db.add(po)
    db.commit()
    db.refresh(po)
    return po


@router.get("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
def get_purchase_order(po_id: str, db: Session = Depends(get_db)) -> PurchaseOrder:
    return _get_po(db, po_id)


@router.patch("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
def update_purchase_order(
    po_id: str, payload: PurchaseOrderUpdate, db: Session = Depends(get_db)
) -> PurchaseOrder:
    """Move a PO through draft → pending_approval → approved/rejected → sent."""
    po = _get_po(db, po_id)
    if payload.status != po.status and payload.status not in PO_TRANSITIONS[po.status]:
        raise APIError(
            409,
            "invalid_transition",
            f"Purchase order cannot move from '{po.status}' to '{payload.status}'",
            {"from": po.status, "to": payload.status, "allowed": sorted(PO_TRANSITIONS[po.status])},
        )
    po.status = payload.status
    db.commit()
    db.refresh(po)
    return po
