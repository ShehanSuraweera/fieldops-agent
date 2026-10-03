"""Test doubles: an in-memory enterprise (same interface as EnterpriseTools) and a scripted LLM."""

import json
import re
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any

from app.config import LK_TZ
from app.enterprise import (
    Asset,
    Customer,
    FaultCode,
    Part,
    PartNeed,
    POLine,
    PurchaseOrder,
    Reservation,
    ServiceRecord,
    Slot,
    TechnicianAvailability,
    Ticket,
    TicketMessage,
    TicketUpdate,
    VendorOffer,
    WorkOrder,
)
from app.llm import LLMRequest, LLMResponse, LLMUsage
from app.rules import RulesConfig, compute_asset_facts
from app.tools import AssetSummary, ToolError, ToolResult

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=LK_TZ)  # Thursday 09:00 Colombo
TODAY = NOW.date()
RULES = RulesConfig(
    sla_hours={"gold": 4, "silver": 24, "bronze": 72},
    po_approval_threshold_lkr=100_000,
    repeat_failure_window_days=90,
    diagnosis_min_confidence=0.6,
    inspection_hours=2,
)


def _ok(data: Any) -> ToolResult:
    return ToolResult.success(data)


def _err(code: str, message: str, status: int = 404) -> ToolResult:
    return ToolResult.failure(ToolError(code=code, message=message, status_code=status))


def _asset(
    asset_id: str, customer: str, name: str, model: str, site: str, region: str, warranty: date
) -> Asset:
    return Asset(
        id=asset_id,
        customer_id=customer,
        name=name,
        model=model,
        serial=f"SN-{asset_id}",
        site=site,
        region=region,
        install_date=date(2025, 8, 8),
        warranty_end=warranty,
    )


class FakeEnterprise:
    """Just enough CRM/FSM/ERP behaviour to drive the graph, with no network."""

    def __init__(self, clock: Callable[[], datetime] = lambda: NOW) -> None:
        self.clock = clock
        self.customers = [
            Customer(
                id="CUST-001",
                name="FreshMart",
                region="Colombo",
                contract_tier="gold",
                contact_email="ops@freshmart.lk",
            ),
            Customer(
                id="CUST-008",
                name="Peradeniya Fresh",
                region="Kandy",
                contract_tier="bronze",
                contact_email="info@peradeniyafresh.lk",
            ),
        ]
        self.assets = [
            _asset("FRZ-1041", "CUST-001", "Freezer #1", "AP-500", "Colombo 7", "Colombo", date(2027, 1, 1)),
            _asset("FRZ-1043", "CUST-001", "Freezer #3", "AP-500", "Colombo 7", "Colombo", date(2027, 8, 8)),
            _asset("CHL-1044", "CUST-001", "Chiller #1", "PM-220", "Nugegoda", "Colombo", date(2025, 1, 1)),
            _asset("FRZ-1010", "CUST-008", "Freezer #1", "FL-140", "Peradeniya", "Kandy", date(2025, 1, 1)),
            _asset("FRZ-1011", "CUST-008", "Freezer #1", "FL-140", "Kandy City", "Kandy", date(2025, 1, 1)),
        ]
        self.history = [
            ServiceRecord(
                id=1,
                asset_id="FRZ-1043",
                date=TODAY - timedelta(days=60),
                fault_code="COMP_FAIL",
                notes="Compressor replaced.",
                parts_used=[],
                technician_id="TECH-02",
            ),
        ]
        self.fault_codes = [
            FaultCode(
                code="COMP_FAIL",
                name="Compressor failure",
                symptoms=["not cooling"],
                applies_to_models=["AP-500", "FL-140", "PM-220"],
                likely_parts=["CMP-AP", "CMP-FL"],
                skill_required="refrigeration",
                est_hours=4,
            ),
            FaultCode(
                code="THERMO_FAIL",
                name="Thermostat failure",
                symptoms=["temperature erratic"],
                applies_to_models=["AP-500", "FL-140", "PM-220"],
                likely_parts=["THM-MECH"],
                skill_required="electrical",
                est_hours=1.5,
            ),
        ]
        self.parts = {
            "CMP-AP": Part(
                sku="CMP-AP",
                name="Compressor AP",
                compatible_models=["AP-500"],
                unit_cost_lkr=92_000,
                stock_qty=2,
                reorder_level=1,
            ),
            "CMP-FL": Part(
                sku="CMP-FL",
                name="Compressor FL",
                compatible_models=["FL-140"],
                unit_cost_lkr=118_000,
                stock_qty=0,
                reorder_level=1,
            ),
            "THM-MECH": Part(
                sku="THM-MECH",
                name="Thermostat",
                compatible_models=["AP-500"],
                unit_cost_lkr=6_500,
                stock_qty=10,
                reorder_level=4,
            ),
        }
        self.offers = [
            VendorOffer(
                vendor_id="VEN-01",
                vendor_name="Ceylon Refrigeration",
                sku="CMP-FL",
                approved=True,
                lead_time_days=2,
                price_lkr=129_000,
            ),
            VendorOffer(
                vendor_id="VEN-03",
                vendor_name="Lanka Industrial",
                sku="CMP-FL",
                approved=True,
                lead_time_days=5,
                price_lkr=115_000,
            ),
            VendorOffer(
                vendor_id="VEN-05",
                vendor_name="QuickParts",
                sku="CMP-FL",
                approved=False,
                lead_time_days=1,
                price_lkr=99_000,
            ),
        ]
        self.technicians = {
            "TECH-01": ("Nimal Perera", "Colombo", ["refrigeration", "electrical"]),
            "TECH-02": ("Kasun Fernando", "Colombo", ["refrigeration", "electrical", "general"]),
            "TECH-04": ("Ruwan Bandara", "Kandy", ["refrigeration", "general"]),
        }
        self.slots: dict[int, tuple[str, datetime, datetime, bool]] = {}
        slot_id = 0
        for offset in range(14):
            day = TODAY + timedelta(days=offset)
            for tech_id in self.technicians:
                for hour in (8, 10, 12, 14, 16):
                    slot_id += 1
                    start = datetime.combine(day, time(hour), tzinfo=LK_TZ)
                    self.slots[slot_id] = (tech_id, start, start + timedelta(hours=2), False)
        self.tickets: dict[str, Ticket] = {}
        self.reservations: list[Reservation] = []
        self.purchase_orders: dict[str, PurchaseOrder] = {}
        self.work_orders: dict[str, WorkOrder] = {}

    # CRM

    def find_customer(self, email: str) -> ToolResult:
        match = [c for c in self.customers if c.contact_email == email.lower()]
        return _ok(match[0]) if match else _err("not_found", f"No customer with email '{email}'")

    def create_ticket(self, customer_id: str, description: str, asset_id: str | None = None) -> ToolResult:
        ticket = Ticket(
            id=f"TKT-{len(self.tickets) + 1:06d}",
            customer_id=customer_id,
            asset_id=asset_id,
            description=description,
            status="new",
            priority=None,
            sla_deadline=None,
            created_at=self.clock(),
            resolution_note=None,
            messages=[],
        )
        self.tickets[ticket.id] = ticket
        return _ok(ticket)

    def update_ticket(self, ticket_id: str, fields: dict[str, Any]) -> ToolResult:
        changes = TicketUpdate.model_validate(fields).model_dump(exclude_unset=True)
        ticket = self.tickets[ticket_id].model_copy(update=changes)
        self.tickets[ticket_id] = ticket
        return _ok(ticket)

    def send_customer_message(self, ticket_id: str, body: str) -> ToolResult:
        ticket = self.tickets[ticket_id]
        message = TicketMessage(
            id=len(ticket.messages) + 1,
            ticket_id=ticket_id,
            direction="outbound",
            body=body,
            created_at=self.clock(),
        )
        ticket.messages.append(message)
        return _ok(message)

    # FSM

    def find_asset(self, customer_id: str, query: str) -> ToolResult:
        words = query.lower().split()
        found = [
            a
            for a in self.assets
            if a.customer_id == customer_id
            and all(any(w in f.lower() for f in (a.id, a.name, a.model, a.site)) for w in words)
        ]
        return _ok(found)

    def get_asset_summary(self, asset_id: str) -> ToolResult:
        asset = next((a for a in self.assets if a.id == asset_id), None)
        if asset is None:
            return _err("not_found", f"asset '{asset_id}' not found")
        history = sorted(
            (h for h in self.history if h.asset_id == asset_id), key=lambda h: h.date, reverse=True
        )
        facts = compute_asset_facts(asset, history, self.clock().date(), RULES)
        return _ok(AssetSummary(asset=asset, facts=facts, recent_history=history[:5]))

    def list_fault_codes(self, model: str) -> ToolResult:
        return _ok([f for f in self.fault_codes if model in f.applies_to_models])

    def find_available_technicians(
        self, region: str, skill: str, earliest: datetime, latest: datetime
    ) -> ToolResult:
        result = []
        for tech_id, (name, tech_region, skills) in self.technicians.items():
            if tech_region != region or skill not in skills:
                continue
            slots = [
                Slot(slot_id=sid, start=start, end=end, jobs_that_day=0)
                for sid, (owner, start, end, booked) in sorted(self.slots.items())
                if owner == tech_id and not booked and start >= earliest and end <= latest
            ]
            if slots:
                result.append(
                    TechnicianAvailability(
                        technician_id=tech_id, name=name, region=tech_region, skills=skills, slots=slots
                    )
                )
        return _ok(result)

    def create_work_order(
        self, ticket_id: str, asset_id: str, technician_id: str, scheduled_start: datetime, est_hours: float
    ) -> ToolResult:
        end = scheduled_start + timedelta(hours=2 * -(-est_hours // 2))
        wanted = [
            sid
            for sid, (owner, start, slot_end, booked) in self.slots.items()
            if owner == technician_id and start >= scheduled_start and slot_end <= end and not booked
        ]
        if len(wanted) * 2 < est_hours:
            return _err("slot_unavailable", "slots taken", 409)
        for sid in wanted:
            owner, start, slot_end, _ = self.slots[sid]
            self.slots[sid] = (owner, start, slot_end, True)
        work_order = WorkOrder(
            id=f"WO-{len(self.work_orders) + 1:06d}",
            ticket_id=ticket_id,
            asset_id=asset_id,
            technician_id=technician_id,
            scheduled_start=scheduled_start,
            est_hours=est_hours,
            status="scheduled",
            created_at=self.clock(),
        )
        self.work_orders[work_order.id] = work_order
        return _ok(work_order)

    # ERP

    def check_part_stock(self, sku: str) -> ToolResult:
        part = self.parts.get(sku)
        return _ok(part) if part else _err("not_found", f"part '{sku}' not found")

    def reserve_part(self, sku: str, qty: int, ticket_id: str) -> ToolResult:
        part = self.parts[sku]
        if part.stock_qty < qty:
            return _err("insufficient_stock", "not enough stock", 409)
        part.stock_qty -= qty
        reservation = Reservation(
            id=len(self.reservations) + 1,
            sku=sku,
            ticket_id=ticket_id,
            qty=qty,
            stock_qty_after=part.stock_qty,
        )
        self.reservations.append(reservation)
        return _ok(reservation)

    def find_vendors(self, sku: str) -> ToolResult:
        return _ok([o for o in self.offers if o.sku == sku])

    def create_purchase_order(
        self, vendor_id: str, lines: list[PartNeed], ticket_id: str, warranty_claim: bool
    ) -> ToolResult:
        prices = {o.sku: o.price_lkr for o in self.offers if o.vendor_id == vendor_id}
        po = PurchaseOrder(
            id=f"PO-{len(self.purchase_orders) + 1:06d}",
            vendor_id=vendor_id,
            ticket_id=ticket_id,
            status="draft",
            total_lkr=sum(prices[line.sku] * line.qty for line in lines),
            warranty_claim=warranty_claim,
            created_by="agent",
            created_at=self.clock(),
            lines=[POLine(sku=line.sku, qty=line.qty, unit_price_lkr=prices[line.sku]) for line in lines],
        )
        self.purchase_orders[po.id] = po
        return _ok(po)

    def update_purchase_order(self, po_id: str, status: str) -> ToolResult:
        po = self.purchase_orders[po_id].model_copy(update={"status": status})
        self.purchase_orders[po_id] = po
        return _ok(po)


# --- scripted LLM ----------------------------------------------------------------

Reply = str | dict[str, Any] | Callable[[LLMRequest], str | dict[str, Any]]


def _fact(user: str, key: str) -> str:
    match = re.search(rf"^- {key}: (.+)$", user, flags=re.M)
    return match.group(1) if match else ""


def default_message(request: LLMRequest) -> dict[str, Any]:
    """A well-behaved customer message built from the facts in the prompt."""
    technician, visit = _fact(request.user, "technician"), _fact(request.user, "visit_time")
    return {
        "body": f"Hello, {technician} will visit on {visit}. "
        "We will update you after the visit. CoolTech Services"
    }


class FakeLLM:
    """LLMProvider that answers each prompt from a script.

    ``script`` maps prompt names to a reply or a list of replies (played in order,
    the last one repeating). Replies may be JSON-able dicts, raw strings, or
    callables taking the request.
    """

    name = "fake"
    model = "fake-1"

    def __init__(self, script: dict[str, Reply | list[Reply]]) -> None:
        self.script = {"customer_message": default_message, **script}
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        replies = self.script[request.prompt_name]
        reply = replies
        if isinstance(replies, list):
            reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if callable(reply):
            reply = reply(request)
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return LLMResponse(text=text, usage=LLMUsage(input_tokens=100, output_tokens=20))

    def calls(self, prompt_name: str) -> list[LLMRequest]:
        return [r for r in self.requests if r.prompt_name == prompt_name]


def intake_reply(asset: str | None, site: str | None = None, *, down: bool = True) -> dict[str, Any]:
    return {
        "asset_description": asset,
        "site_hint": site,
        "symptoms": ["not cooling"],
        "asset_down": down,
        "urgency_cues": [],
    }


def diagnosis_reply(code: str = "COMP_FAIL", confidence: float = 0.9) -> dict[str, Any]:
    return {
        "fault_code": code,
        "confidence": confidence,
        "reasoning": "Not cooling after a recent compressor job.",
    }
