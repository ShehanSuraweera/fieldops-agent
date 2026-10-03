"""prioritize: priority, SLA deadline and rule-driven ticket notes."""

from typing import Any

from app.nodes.common import Deps, now_of, require
from app.rules import AssetFacts, priority, sla_deadline, ticket_notes
from app.state import IntakeExtraction, TicketState


def prioritize(state: TicketState, deps: Deps) -> dict[str, Any]:
    tier = state["customer"]["contract_tier"]
    facts = AssetFacts.model_validate(state["asset_facts"])
    extraction = IntakeExtraction.model_validate(state["intake"])

    level = priority(tier, extraction.asset_down, facts.repeat_failure_90d)
    deadline = sla_deadline(now_of(state), tier, deps.rules)
    notes = ticket_notes(facts)
    require(
        deps.tools.update_ticket(
            ticket_id=state["ticket_id"],
            fields={"priority": level, "sla_deadline": deadline, "resolution_note": "\n".join(notes) or None},
        ),
        "update_ticket",
    )
    return {"priority": level, "sla_deadline": deadline.isoformat(), "ticket_notes": notes}
