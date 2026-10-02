"""Pydantic v2 request and response models for the mock enterprise API."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ContractTier = Literal["gold", "silver", "bronze"]
TicketStatus = Literal[
    "new",
    "open",
    "needs_info",
    "awaiting_approval",
    "scheduled",
    "needs_human",
    "needs_manual_procurement",
    "closed",
]
Priority = Literal["P1", "P2", "P3"]
MessageDirection = Literal["inbound", "outbound"]
POStatus = Literal["draft", "pending_approval", "approved", "rejected", "sent"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class InputModel(BaseModel):
    """Request bodies reject unknown fields so typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


# --- CRM ---------------------------------------------------------------------


class CustomerOut(ORMModel):
    id: str
    name: str
    region: str
    contract_tier: ContractTier
    contact_email: str


class TicketMessageOut(ORMModel):
    id: int
    ticket_id: str
    direction: MessageDirection
    body: str
    created_at: datetime


class TicketCreate(InputModel):
    customer_id: str
    asset_id: str | None = None
    description: str = Field(min_length=1)
    status: TicketStatus = "new"
    priority: Priority | None = None
    sla_deadline: datetime | None = None


class TicketUpdate(InputModel):
    asset_id: str | None = None
    status: TicketStatus | None = None
    priority: Priority | None = None
    sla_deadline: datetime | None = None
    resolution_note: str | None = None


class TicketOut(ORMModel):
    id: str
    customer_id: str
    asset_id: str | None
    description: str
    status: TicketStatus
    priority: Priority | None
    sla_deadline: datetime | None
    created_at: datetime
    resolution_note: str | None
    messages: list[TicketMessageOut]


class TicketMessageCreate(InputModel):
    body: str = Field(min_length=1)
    direction: MessageDirection = "outbound"


# --- FSM ---------------------------------------------------------------------


class AssetOut(ORMModel):
    id: str
    customer_id: str
    name: str
    model: str
    serial: str
    site: str
    region: str
    install_date: date
    warranty_end: date


class PartUsed(BaseModel):
    sku: str
    qty: int


class ServiceRecordOut(ORMModel):
    id: int
    asset_id: str
    date: date
    fault_code: str
    notes: str
    parts_used: list[PartUsed]
    technician_id: str


class FaultCodeOut(ORMModel):
    code: str
    name: str
    symptoms: list[str]
    applies_to_models: list[str]
    likely_parts: list[str]
    skill_required: str
    est_hours: float


class SlotOut(BaseModel):
    slot_id: int
    start: datetime
    end: datetime
    jobs_that_day: int = Field(description="Slots already booked for this technician on this date")


class TechnicianAvailabilityOut(BaseModel):
    technician_id: str
    name: str
    region: str
    skills: list[str]
    slots: list[SlotOut]


class WorkOrderCreate(InputModel):
    ticket_id: str
    asset_id: str
    technician_id: str
    scheduled_start: datetime = Field(
        description="Must match the start of an open slot; naive = Colombo time"
    )
    est_hours: float = Field(gt=0, le=10)


class WorkOrderOut(ORMModel):
    id: str
    ticket_id: str
    asset_id: str
    technician_id: str
    scheduled_start: datetime
    est_hours: float
    status: str
    created_at: datetime


# --- ERP ---------------------------------------------------------------------


class PartOut(ORMModel):
    sku: str
    name: str
    compatible_models: list[str]
    unit_cost_lkr: int
    stock_qty: int
    reorder_level: int


class ReservationCreate(InputModel):
    sku: str
    qty: int = Field(gt=0)
    ticket_id: str


class ReservationOut(BaseModel):
    id: int
    sku: str
    ticket_id: str
    qty: int
    stock_qty_after: int


class VendorOut(BaseModel):
    id: str
    name: str
    approved: bool
    lead_time_days: int
    price_lkr: int | None = Field(None, description="Set when the list is filtered by sku")


class POLineIn(InputModel):
    sku: str
    qty: int = Field(gt=0)


class PurchaseOrderCreate(InputModel):
    vendor_id: str
    ticket_id: str | None = None
    lines: list[POLineIn] = Field(min_length=1)
    warranty_claim: bool = False
    created_by: str = "agent"


class PurchaseOrderUpdate(InputModel):
    status: POStatus


class POLineOut(ORMModel):
    sku: str
    qty: int
    unit_price_lkr: int


class PurchaseOrderOut(ORMModel):
    id: str
    vendor_id: str
    ticket_id: str | None
    status: POStatus
    total_lkr: int
    warranty_claim: bool
    created_by: str
    created_at: datetime
    lines: list[POLineOut]
