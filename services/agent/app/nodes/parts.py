"""plan_parts and procure: reserve what is in stock, buy the rest from the right vendor."""

from datetime import datetime
from typing import Any

from app.enterprise import PartNeed
from app.nodes.common import Deps, NodeFailure, dump, fail, now_of, require
from app.rules import AssetFacts, choose_vendor, plan_parts, po_requires_approval
from app.state import TicketState


def plan_parts_node(state: TicketState, deps: Deps) -> dict[str, Any]:
    needs = [PartNeed.model_validate(p) for p in state["diagnosis"]["parts"]]
    if not needs:
        return {"parts_plan": []}

    stock = {
        n.sku: require(deps.tools.check_part_stock(sku=n.sku), "check_part_stock").stock_qty for n in needs
    }
    plan = plan_parts(needs, stock)
    for item in plan:
        if item.status != "in_stock":
            continue
        reservation = deps.tools.reserve_part(sku=item.sku, qty=item.qty, ticket_id=state["ticket_id"])
        if reservation.ok:
            item.status = "reserved"
        elif reservation.error.code == "insufficient_stock":  # taken since we checked
            item.status = "needs_po"
        else:
            raise NodeFailure(f"reserve_part failed ({reservation.error.code}): {reservation.error.message}")
    return {"parts_plan": [item.model_dump() for item in plan]}


def procure(state: TicketState, deps: Deps) -> dict[str, Any]:
    to_order = [
        PartNeed(sku=p["sku"], qty=p["qty"]) for p in state["parts_plan"] if p["status"] == "needs_po"
    ]
    if not to_order:
        return {"purchase_order": None, "approval": None, "parts_ready_at": None}

    now = now_of(state)
    deadline = datetime.fromisoformat(state["sla_deadline"])
    offers = [
        offer for need in to_order for offer in require(deps.tools.find_vendors(sku=need.sku), "find_vendors")
    ]
    choice = choose_vendor(to_order, offers, now, deadline)
    if choice is None:
        skus = ", ".join(n.sku for n in to_order)
        return fail(state, f"No single approved vendor supplies all of: {skus}.")

    facts = AssetFacts.model_validate(state["asset_facts"])
    po = require(
        deps.tools.create_purchase_order(
            vendor_id=choice.vendor_id,
            lines=to_order,
            ticket_id=state["ticket_id"],
            warranty_claim=facts.under_warranty,
        ),
        "create_purchase_order",
    )
    # The ERP's total is authoritative; the approval rule is applied to it.
    required = po_requires_approval(po.total_lkr, deps.rules)
    # Human approval arrives in Phase 4; until then every PO is approved automatically.
    for status in (["pending_approval"] if required else []) + ["approved", "sent"]:
        po = require(deps.tools.update_purchase_order(po_id=po.id, status=status), "update_purchase_order")
    approval = {
        "required": required,
        "decision": "approve",
        "comment": "Auto-approved (manager approval not enabled yet)."
        if required
        else "At or below the approval threshold; approved automatically.",
    }
    return {
        "purchase_order": {
            **dump(po),
            "vendor_name": choice.vendor_name,
            "lead_time_days": choice.lead_time_days,
            "parts_ready_at": choice.parts_ready_at.isoformat(),
            "sla_risk": choice.sla_risk,
            "reason": choice.reason,
        },
        "approval": approval,
        "parts_ready_at": choice.parts_ready_at.isoformat(),
    }
