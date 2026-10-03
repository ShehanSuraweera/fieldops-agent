"""identify: match the complaint to exactly one asset, or ask the customer which one."""

from typing import Any

from app.enterprise import Asset
from app.nodes.common import Deps, dump, fail, require
from app.state import ClarifyingQuestion, IntakeExtraction, TicketState


def _at_site(asset: Asset, hint: str) -> bool:
    site, hint = asset.site.lower(), hint.lower().strip()
    return bool(hint) and (hint in site or site in hint)


def identify(state: TicketState, deps: Deps) -> dict[str, Any]:
    extraction = IntakeExtraction.model_validate(state["intake"])
    customer_id = state["customer"]["id"]

    candidates: list[Asset] = []
    if extraction.asset_description:
        candidates = require(
            deps.tools.find_asset(customer_id=customer_id, query=extraction.asset_description), "find_asset"
        )
    if not candidates:  # unnamed or unmatched: consider every asset the customer has
        candidates = require(deps.tools.find_asset(customer_id=customer_id, query=""), "find_asset")
    if len(candidates) > 1 and extraction.site_hint:
        at_site = [a for a in candidates if _at_site(a, extraction.site_hint)]
        candidates = at_site or candidates

    if len(candidates) == 1:
        return {"asset": dump(candidates[0])}
    if not candidates:
        return fail(state, f"Customer {customer_id} has no assets on file.")

    listing = "\n".join(f"- {a.name} at {a.site} (model {a.model})" for a in candidates)
    question = deps.llm.generate(
        "clarify",
        {"customer_name": state["customer"]["name"], "complaint": state["raw_text"], "candidates": listing},
        ClarifyingQuestion,
    )
    ticket_id = state["ticket_id"]
    require(
        deps.tools.send_customer_message(ticket_id=ticket_id, body=question.question), "send_customer_message"
    )
    require(deps.tools.update_ticket(ticket_id=ticket_id, fields={"status": "needs_info"}), "update_ticket")
    return {"status": "needs_info", "customer_message": question.question}
