"""intake: find the sender, extract the complaint's facts, open the CRM ticket."""

from typing import Any

from app.nodes.common import Deps, NodeFailure, dump, fail, require
from app.state import IntakeExtraction, TicketState


def intake(state: TicketState, deps: Deps) -> dict[str, Any]:
    email = state["customer_email"]
    customer = deps.tools.find_customer(email=email)
    if not customer.ok:
        if customer.error.code == "not_found":
            return fail(state, f"No customer matches sender {email}; a coordinator must identify them.")
        raise NodeFailure(f"find_customer failed ({customer.error.code}): {customer.error.message}")

    extraction = deps.llm.generate("intake", {"complaint": state["raw_text"]}, IntakeExtraction)
    ticket = require(
        deps.tools.create_ticket(customer_id=customer.data.id, description=state["raw_text"]), "create_ticket"
    )
    return {"customer": dump(customer.data), "intake": extraction.model_dump(), "ticket_id": ticket.id}
