"""plan_parts and procure: reserve what is in stock, buy the rest from the right vendor."""

from datetime import datetime
from typing import Any, Literal

from langgraph.types import interrupt
from pydantic import BaseModel

from app.enterprise import PartNeed
from app.nodes.common import Deps, NodeFailure, dump, fail, now_of, require
from app.rules import AssetFacts, choose_vendor, parts_ready_at, plan_parts, po_requires_approval
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
    """Choose the vendor and draft the PO. Over the threshold, park it for manager approval.

    Everything with side effects happens here, before the pause: LangGraph re-runs an
    interrupted node from the top on resume, so the interrupt lives in approve_po.
    """
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
    if required:
        po = require(
            deps.tools.update_purchase_order(po_id=po.id, status="pending_approval"), "update_purchase_order"
        )
        require(
            deps.tools.update_ticket(ticket_id=state["ticket_id"], fields={"status": "awaiting_approval"}),
            "update_ticket",
        )
        approval = {"required": True, "decision": None, "comment": None, "requested_at": now.isoformat()}
    else:
        for status in ("approved", "sent"):
            po = require(
                deps.tools.update_purchase_order(po_id=po.id, status=status), "update_purchase_order"
            )
        approval = {
            "required": False,
            "decision": "approve",
            "comment": "At or below the approval threshold; approved automatically.",
            "decided_by": "system",
        }
    update: dict[str, Any] = {
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
    if required:
        update["status"] = "awaiting_approval"
    return update


class ApprovalDecision(BaseModel):
    """The resume value a manager's decision is passed back into the graph with."""

    decision: Literal["approve", "reject"]
    comment: str | None = None
    decided_at: datetime


def approve_po(state: TicketState, deps: Deps) -> dict[str, Any]:
    """Pause for the manager, then apply the decision.

    On the first pass interrupt() suspends the run (state is in the checkpointer).
    On resume this node runs again from the top and interrupt() returns the decision.
    """
    po = state["purchase_order"]
    answer = interrupt(
        {
            "type": "po_approval",
            "run_id": state["run_id"],
            "ticket_id": state["ticket_id"],
            "purchase_order": po,
            "diagnosis": state["diagnosis"],
        }
    )
    decision = ApprovalDecision.model_validate(answer)
    approval = {
        **state["approval"],
        "decision": decision.decision,
        "comment": decision.comment,
        "decided_at": decision.decided_at.isoformat(),
        "decided_by": "manager",
    }

    if decision.decision == "approve":
        for status in ("approved", "sent"):
            updated = require(
                deps.tools.update_purchase_order(po_id=po["id"], status=status), "update_purchase_order"
            )
        # The vendor starts its lead time when the PO is sent, i.e. at approval.
        ready = parts_ready_at(decision.decided_at, po["lead_time_days"]).isoformat()
        return {
            "status": "running",
            "approval": approval,
            "purchase_order": {**po, "status": updated.status, "parts_ready_at": ready},
            "parts_ready_at": ready,
        }

    updated = require(
        deps.tools.update_purchase_order(po_id=po["id"], status="rejected"), "update_purchase_order"
    )
    require(
        deps.tools.update_ticket(ticket_id=state["ticket_id"], fields={"status": "needs_manual_procurement"}),
        "update_ticket",
    )
    return {
        "status": "running",
        "approval": approval,
        "purchase_order": {**po, "status": updated.status},
        "parts_ready_at": None,  # nothing is coming; book an inspection visit now
    }
