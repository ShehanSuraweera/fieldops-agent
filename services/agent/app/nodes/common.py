"""Shared plumbing for graph nodes."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.llm import StructuredLLM
from app.rules import RulesConfig
from app.state import TicketState
from app.tools import ToolResult


@dataclass
class Deps:
    tools: Any  # EnterpriseTools (wrapped in LoggedTools), called with keyword arguments
    llm: StructuredLLM
    rules: RulesConfig


class NodeFailure(Exception):
    """A step cannot continue; the run is routed to human review."""


def require[T](result: ToolResult[T], what: str) -> T:
    if not result.ok:
        raise NodeFailure(f"{what} failed ({result.error.code}): {result.error.message}")
    return result.data


def fail(state: TicketState, reason: str) -> dict[str, Any]:
    return {"status": "needs_human", "errors": [*state.get("errors", []), reason]}


def dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def now_of(state: TicketState) -> datetime:
    return datetime.fromisoformat(state["now"])


def procurement_rejected(state: TicketState) -> bool:
    """A manager rejected the PO: the visit becomes an inspection while parts are sourced manually."""
    return (state.get("approval") or {}).get("decision") == "reject"


def visit_hours(state: TicketState, deps: Deps) -> float:
    if procurement_rejected(state):
        return deps.rules.inspection_hours
    return state["diagnosis"]["est_hours"]
