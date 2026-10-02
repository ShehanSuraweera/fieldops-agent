"""SQLAlchemy models for the three mock enterprise systems.

One Postgres database holds a schema per system: ``crm``, ``fsm`` and ``erp``.
The ``agent`` schema is created by the migration and filled in later phases.
Money is stored as whole Sri Lankan rupees (integers) so amounts stay exact.
"""

import datetime as dt
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Sequence,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMAS = ("crm", "fsm", "erp", "agent")


class Base(DeclarativeBase):
    pass


# Sequences behind the human-readable ids (TKT-000001, WO-000001, PO-000001).
ticket_id_seq = Sequence("ticket_id_seq", schema="crm", metadata=Base.metadata)
work_order_id_seq = Sequence("work_order_id_seq", schema="fsm", metadata=Base.metadata)
purchase_order_id_seq = Sequence("purchase_order_id_seq", schema="erp", metadata=Base.metadata)
ID_SEQUENCES = (ticket_id_seq, work_order_id_seq, purchase_order_id_seq)


# --- CRM ---------------------------------------------------------------------


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("contract_tier IN ('gold', 'silver', 'bronze')", name="ck_customers_tier"),
        {"schema": "crm"},
    )

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    region: Mapped[str] = mapped_column(String(50))
    contract_tier: Mapped[str] = mapped_column(String(10))
    contact_email: Mapped[str] = mapped_column(String(200), unique=True)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("priority IN ('P1', 'P2', 'P3')", name="ck_tickets_priority"),
        {"schema": "crm"},
    )

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("crm.customers.id"))
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("fsm.assets.id"))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="new")
    priority: Mapped[str | None] = mapped_column(String(2))
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolution_note: Mapped[str | None] = mapped_column(Text)

    messages: Mapped[list["TicketMessage"]] = relationship(order_by="TicketMessage.id")


class TicketMessage(Base):
    __tablename__ = "ticket_messages"
    __table_args__ = (
        CheckConstraint("direction IN ('inbound', 'outbound')", name="ck_ticket_messages_direction"),
        {"schema": "crm"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("crm.tickets.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- FSM ---------------------------------------------------------------------


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = {"schema": "fsm"}

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("crm.customers.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(50))
    serial: Mapped[str] = mapped_column(String(50), unique=True)
    site: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(50))
    install_date: Mapped[date] = mapped_column(Date)
    warranty_end: Mapped[date] = mapped_column(Date)


class FaultCode(Base):
    __tablename__ = "fault_codes"
    __table_args__ = {"schema": "fsm"}

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    symptoms: Mapped[list[str]] = mapped_column(ARRAY(Text))
    applies_to_models: Mapped[list[str]] = mapped_column(ARRAY(String(50)))
    likely_parts: Mapped[list[str]] = mapped_column(ARRAY(String(30)))
    skill_required: Mapped[str] = mapped_column(String(30))
    est_hours: Mapped[float] = mapped_column(Float)


class Technician(Base):
    __tablename__ = "technicians"
    __table_args__ = {"schema": "fsm"}

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(50))
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(30)))


class ServiceHistory(Base):
    __tablename__ = "service_history"
    __table_args__ = {"schema": "fsm"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("fsm.assets.id"), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    fault_code: Mapped[str] = mapped_column(ForeignKey("fsm.fault_codes.code"))
    notes: Mapped[str] = mapped_column(Text)
    # List of {"sku": str, "qty": int}.
    parts_used: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    technician_id: Mapped[str] = mapped_column(ForeignKey("fsm.technicians.id"))


class Availability(Base):
    """One bookable slot in a technician's calendar."""

    __tablename__ = "availability"
    __table_args__ = (
        UniqueConstraint("technician_id", "date", "start", name="uq_availability_slot"),
        {"schema": "fsm"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    technician_id: Mapped[str] = mapped_column(ForeignKey("fsm.technicians.id"))
    date: Mapped[dt.date] = mapped_column(Date)
    start: Mapped[time] = mapped_column(Time)
    end: Mapped[time] = mapped_column(Time)
    booked: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkOrder(Base):
    __tablename__ = "work_orders"
    __table_args__ = {"schema": "fsm"}

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("crm.tickets.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("fsm.assets.id"))
    technician_id: Mapped[str] = mapped_column(ForeignKey("fsm.technicians.id"))
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    est_hours: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- ERP ---------------------------------------------------------------------


class Part(Base):
    __tablename__ = "parts"
    __table_args__ = (
        CheckConstraint("stock_qty >= 0", name="ck_parts_stock_non_negative"),
        {"schema": "erp"},
    )

    sku: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    compatible_models: Mapped[list[str]] = mapped_column(ARRAY(String(50)))
    unit_cost_lkr: Mapped[int] = mapped_column(Integer)
    stock_qty: Mapped[int] = mapped_column(Integer)
    reorder_level: Mapped[int] = mapped_column(Integer)


class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = {"schema": "erp"}

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    approved: Mapped[bool] = mapped_column(Boolean)
    lead_time_days: Mapped[int] = mapped_column(Integer)


class VendorPart(Base):
    __tablename__ = "vendor_parts"
    __table_args__ = {"schema": "erp"}

    vendor_id: Mapped[str] = mapped_column(ForeignKey("erp.vendors.id"), primary_key=True)
    sku: Mapped[str] = mapped_column(ForeignKey("erp.parts.sku"), primary_key=True)
    price_lkr: Mapped[int] = mapped_column(Integer)


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (CheckConstraint("qty > 0", name="ck_reservations_qty"), {"schema": "erp"})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(ForeignKey("erp.parts.sku"))
    ticket_id: Mapped[str] = mapped_column(ForeignKey("crm.tickets.id"), index=True)
    qty: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'rejected', 'sent')",
            name="ck_purchase_orders_status",
        ),
        {"schema": "erp"},
    )

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("erp.vendors.id"))
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("crm.tickets.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    total_lkr: Mapped[int] = mapped_column(Integer)
    warranty_claim: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["POLine"]] = relationship(order_by="POLine.sku")


class POLine(Base):
    __tablename__ = "po_lines"
    __table_args__ = (CheckConstraint("qty > 0", name="ck_po_lines_qty"), {"schema": "erp"})

    po_id: Mapped[str] = mapped_column(ForeignKey("erp.purchase_orders.id"), primary_key=True)
    sku: Mapped[str] = mapped_column(ForeignKey("erp.parts.sku"), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer)
    unit_price_lkr: Mapped[int] = mapped_column(Integer)
