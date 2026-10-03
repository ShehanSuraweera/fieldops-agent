"""schedule: book the best technician once the parts are available."""

from datetime import datetime, timedelta
from typing import Any

from app.nodes.common import Deps, NodeFailure, dump, fail, now_of, require, visit_hours
from app.rules import AssetFacts, choose_technician
from app.state import TicketState

SEARCH_WINDOW_DAYS = 14  # how far ahead the FSM calendar is searched


def schedule(state: TicketState, deps: Deps) -> dict[str, Any]:
    asset, diagnosis = state["asset"], state["diagnosis"]
    facts = AssetFacts.model_validate(state["asset_facts"])
    deadline = datetime.fromisoformat(state["sla_deadline"])
    # Not before the run, not before a manager's decision, not before the parts arrive.
    earliest = now_of(state)
    for later in ((state.get("approval") or {}).get("decided_at"), state.get("parts_ready_at")):
        if later:
            earliest = max(earliest, datetime.fromisoformat(later))
    latest = earliest + timedelta(days=SEARCH_WINDOW_DAYS)
    hours = visit_hours(state, deps)

    # Two tries: if the chosen slot is taken between search and booking, search again.
    for attempt in (1, 2):
        technicians = require(
            deps.tools.find_available_technicians(
                region=asset["region"], skill=diagnosis["skill"], earliest=earliest, latest=latest
            ),
            "find_available_technicians",
        )
        choice = choose_technician(
            technicians,
            region=asset["region"],
            skill=diagnosis["skill"],
            prior_technician_ids=facts.previous_technician_ids,
            earliest=earliest,
            sla_deadline=deadline,
            est_hours=hours,
        )
        if choice is None:
            return fail(
                state,
                f"No technician with skill '{diagnosis['skill']}' is free in {asset['region']} "
                f"in the next {SEARCH_WINDOW_DAYS} days.",
            )
        booking = deps.tools.create_work_order(
            ticket_id=state["ticket_id"],
            asset_id=asset["id"],
            technician_id=choice.technician_id,
            scheduled_start=choice.start,
            est_hours=hours,
        )
        if booking.ok:
            return {
                "work_order": {
                    **dump(booking.data),
                    "scheduled_start": choice.start.isoformat(),  # Colombo time, not the ERP's UTC
                    "technician_name": choice.technician_name,
                    "scheduled_end": choice.end.isoformat(),
                    "sla_risk": choice.sla_risk,
                    "reason": choice.reason,
                }
            }
        if booking.error.code != "slot_unavailable" or attempt == 2:
            raise NodeFailure(f"create_work_order failed ({booking.error.code}): {booking.error.message}")
    raise AssertionError("unreachable")
