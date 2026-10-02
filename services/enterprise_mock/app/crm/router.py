"""CRM: customers, support tickets and the customer message log."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import APIError, invalid_reference, not_found
from app.models import Asset, Customer, Ticket, TicketMessage, ticket_id_seq
from app.schemas import (
    CustomerOut,
    TicketCreate,
    TicketMessageCreate,
    TicketMessageOut,
    TicketOut,
    TicketUpdate,
)

router = APIRouter()


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(
    email: str | None = Query(None, description="Exact contact email, case-insensitive"),
    db: Session = Depends(get_db),
) -> list[Customer]:
    stmt = select(Customer).order_by(Customer.id)
    if email:
        stmt = stmt.where(func.lower(Customer.contact_email) == email.strip().lower())
    return list(db.scalars(stmt))


@router.get("/customers/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: str, db: Session = Depends(get_db)) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise not_found("customer", customer_id)
    return customer


def _check_asset_belongs(db: Session, asset_id: str, customer_id: str) -> None:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise invalid_reference("asset", asset_id)
    if asset.customer_id != customer_id:
        raise APIError(
            422,
            "asset_customer_mismatch",
            f"Asset '{asset_id}' does not belong to customer '{customer_id}'",
            {"asset_id": asset_id, "customer_id": customer_id},
        )


def _get_ticket(db: Session, ticket_id: str) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise not_found("ticket", ticket_id)
    return ticket


@router.post("/tickets", response_model=TicketOut, status_code=201)
def create_ticket(payload: TicketCreate, db: Session = Depends(get_db)) -> Ticket:
    if db.get(Customer, payload.customer_id) is None:
        raise invalid_reference("customer", payload.customer_id)
    if payload.asset_id is not None:
        _check_asset_belongs(db, payload.asset_id, payload.customer_id)

    seq = db.scalar(select(ticket_id_seq.next_value()))
    ticket = Ticket(id=f"TKT-{seq:06d}", **payload.model_dump())
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: str, db: Session = Depends(get_db)) -> Ticket:
    return _get_ticket(db, ticket_id)


@router.patch("/tickets/{ticket_id}", response_model=TicketOut)
def update_ticket(ticket_id: str, payload: TicketUpdate, db: Session = Depends(get_db)) -> Ticket:
    ticket = _get_ticket(db, ticket_id)
    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] is None:
        raise APIError(422, "validation_error", "status cannot be null")
    if changes.get("asset_id") is not None:
        _check_asset_belongs(db, changes["asset_id"], ticket.customer_id)

    for field, value in changes.items():
        setattr(ticket, field, value)
    db.commit()
    db.refresh(ticket)
    return ticket


@router.post("/tickets/{ticket_id}/messages", response_model=TicketMessageOut, status_code=201)
def add_ticket_message(
    ticket_id: str, payload: TicketMessageCreate, db: Session = Depends(get_db)
) -> TicketMessage:
    """Log a customer message. Outbound messages stand in for real email or SMS."""
    _get_ticket(db, ticket_id)
    message = TicketMessage(ticket_id=ticket_id, direction=payload.direction, body=payload.body)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message
