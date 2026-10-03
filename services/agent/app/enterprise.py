"""The agent's typed view of the mock CRM, FSM and ERP APIs.

These mirror the enterprise_mock response schemas but are owned by the agent:
the two services share an HTTP contract, not code. Unknown fields are ignored
so the mock can grow without breaking the agent.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ContractTier = Literal["gold", "silver", "bronze"]
Priority = Literal["P1", "P2", "P3"]
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
POStatus = Literal["draft", "pending_approval", "approved", "rejected", "sent"]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --- CRM ---------------------------------------------------------------------


class Customer(APIModel):
    id: str
    name: str
    region: str
    contract_tier: ContractTier
    contact_email: str


class TicketMessage(APIModel):
    id: int
    ticket_id: str
    direction: Literal["inbound", "outbound"]
    body: str
    created_at: datetime


class Ticket(APIModel):
    id: str
    customer_id: str
    asset_id: str | None
    description: str
    status: TicketStatus
    priority: Priority | None
    sla_deadline: datetime | None
    created_at: datetime
    resolution_note: str | None
    messages: list[TicketMessage] = []


class TicketUpdate(BaseModel):
    """Fields the agent may change on a ticket. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str | None = None
    status: TicketStatus | None = None
    priority: Priority | None = None
    sla_deadline: datetime | None = None
    resolution_note: str | None = None


# --- FSM ---------------------------------------------------------------------


class Asset(APIModel):
    id: str
    customer_id: str
    name: str
    model: str
    serial: str
    site: str
    region: str
    install_date: date
    warranty_end: date


class PartUsed(APIModel):
    sku: str
    qty: int


class ServiceRecord(APIModel):
    id: int
    asset_id: str
    date: date
    fault_code: str
    notes: str
    parts_used: list[PartUsed]
    technician_id: str


class FaultCode(APIModel):
    code: str
    name: str
    symptoms: list[str]
    applies_to_models: list[str]
    likely_parts: list[str]
    skill_required: str
    est_hours: float


class Slot(APIModel):
    slot_id: int
    start: datetime
    end: datetime
    jobs_that_day: int


class TechnicianAvailability(APIModel):
    technician_id: str
    name: str
    region: str
    skills: list[str]
    slots: list[Slot]


class WorkOrder(APIModel):
    id: str
    ticket_id: str
    asset_id: str
    technician_id: str
    scheduled_start: datetime
    est_hours: float
    status: str
    created_at: datetime


# --- ERP ---------------------------------------------------------------------


class Part(APIModel):
    sku: str
    name: str
    compatible_models: list[str]
    unit_cost_lkr: int
    stock_qty: int
    reorder_level: int


class Reservation(APIModel):
    id: int
    sku: str
    ticket_id: str
    qty: int
    stock_qty_after: int


class VendorOffer(APIModel):
    """One vendor's price and lead time for one sku."""

    vendor_id: str
    vendor_name: str
    sku: str
    approved: bool
    lead_time_days: int
    price_lkr: int


class POLine(APIModel):
    sku: str
    qty: int
    unit_price_lkr: int


class PurchaseOrder(APIModel):
    id: str
    vendor_id: str
    ticket_id: str | None
    status: POStatus
    total_lkr: int
    warranty_claim: bool
    created_by: str
    created_at: datetime
    lines: list[POLine]


class PartNeed(BaseModel):
    """A part and quantity the job needs."""

    sku: str
    qty: int = Field(default=1, gt=0)
