"""finalize: confirm the booking to the customer and close out the ticket; escalate otherwise."""

from datetime import datetime
from typing import Any

from app.config import LK_TZ
from app.nodes.common import Deps, require
from app.state import CustomerMessage, TicketState


def visit_time_text(start: datetime) -> str:
    """e.g. 'Thursday 1 October 2026 at 10:00' (Colombo time)."""
    local = start.astimezone(LK_TZ)
    return f"{local:%A} {local.day} {local:%B %Y} at {local:%H:%M}"


def _booking_facts(state: TicketState) -> tuple[dict[str, str], list[str]]:
    work_order, diagnosis, asset = state["work_order"], state["diagnosis"], state["asset"]
    start = datetime.fromisoformat(work_order["scheduled_start"])
    facts = {
        "customer": state["customer"]["name"],
        "unit": f"{asset['name']} at {asset['site']}",
        "ticket_reference": state["ticket_id"],
        "technician": work_order["technician_name"],
        "visit_time": visit_time_text(start),
    }
    if diagnosis["inspection"]:
        facts["visit_purpose"] = "Inspection visit to confirm the fault before any parts are ordered."
    else:
        facts["visit_purpose"] = f"Repair visit for: {diagnosis['fault_name']}."
        statuses = {p["status"] for p in state.get("parts_plan", [])}
        if "needs_po" in statuses:
            facts["parts"] = "Replacement parts have been ordered and will arrive before the visit."
        elif statuses:
            facts["parts"] = "Replacement parts are reserved for this visit."
    if state["asset_facts"]["under_warranty"]:
        facts["warranty"] = "The unit is under warranty."
    must_include = [work_order["technician_name"], f"{start.astimezone(LK_TZ):%H:%M}"]
    return facts, must_include


def finalize(state: TicketState, deps: Deps) -> dict[str, Any]:
    ticket_id, work_order = state["ticket_id"], state["work_order"]
    facts, must_include = _booking_facts(state)
    message = deps.llm.generate(
        "customer_message",
        {"facts": "\n".join(f"- {key}: {value}" for key, value in facts.items())},
        CustomerMessage,
        context={"must_include": must_include},
    )
    require(deps.tools.send_customer_message(ticket_id=ticket_id, body=message.body), "send_customer_message")

    lines = list(state.get("ticket_notes", []))
    lines.append(
        f"Scheduled: {work_order['technician_name']} ({work_order['technician_id']}) "
        f"on {facts['visit_time']}, "
        f"work order {work_order['id']}."
    )
    if state.get("purchase_order"):
        po = state["purchase_order"]
        lines.append(f"Parts ordered on {po['id']} from {po['vendor_name']} (LKR {po['total_lkr']:,}).")
    if work_order["sla_risk"] or (state.get("purchase_order") or {}).get("sla_risk"):
        lines.append("SLA at risk: the visit is after the SLA deadline.")
    require(
        deps.tools.update_ticket(
            ticket_id=ticket_id, fields={"status": "scheduled", "resolution_note": "\n".join(lines)}
        ),
        "update_ticket",
    )
    return {"status": "scheduled", "customer_message": message.body}


def escalate(state: TicketState, deps: Deps) -> dict[str, Any]:
    """Hand the ticket to a coordinator. Best effort: the run is already needs_human."""
    if state.get("ticket_id"):
        reason = (state.get("errors") or ["unknown reason"])[-1]
        lines = [*state.get("ticket_notes", []), f"Needs human review: {reason}"]
        deps.tools.update_ticket(
            ticket_id=state["ticket_id"],
            fields={"status": "needs_human", "resolution_note": "\n".join(lines)},
        )
    return {}
