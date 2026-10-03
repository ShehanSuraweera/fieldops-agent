"""assess_asset: warranty, repeat failure, age and history, all computed by rules."""

from typing import Any

from app.nodes.common import Deps, dump, require
from app.state import TicketState


def assess_asset(state: TicketState, deps: Deps) -> dict[str, Any]:
    asset_id = state["asset"]["id"]
    summary = require(deps.tools.get_asset_summary(asset_id=asset_id), "get_asset_summary")
    require(
        deps.tools.update_ticket(
            ticket_id=state["ticket_id"], fields={"asset_id": asset_id, "status": "open"}
        ),
        "update_ticket",
    )
    return {
        "asset": dump(summary.asset),
        "asset_facts": dump(summary.facts),
        "history": [dump(record) for record in summary.recent_history],
    }
