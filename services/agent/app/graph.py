"""The LangGraph workflow and the Agent that runs it.

intake -> identify -> assess_asset -> diagnose -> prioritize -> plan_parts -> procure -> schedule -> finalize

After every node the status decides the route: "running" continues, "needs_human"
goes to escalate, and anything else (needs_info, scheduled) ends the run.
"""

import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.config import agent_now, now_lk
from app.llm import LLMError, LLMProvider, StructuredLLM, provider_from_env
from app.nodes.assess import assess_asset
from app.nodes.common import Deps, NodeFailure, fail
from app.nodes.diagnose import diagnose
from app.nodes.finalize import escalate, finalize
from app.nodes.identify import identify
from app.nodes.intake import intake
from app.nodes.parts import plan_parts_node, procure
from app.nodes.prioritize import prioritize
from app.nodes.schedule import schedule
from app.recorder import LoggedTools, StepRecorder, jsonable
from app.rules import RulesConfig, load_rules
from app.state import TicketState, initial_state
from app.store import RunStore, StepRecord
from app.tools import EnterpriseClient, EnterpriseTools

NodeFn = Callable[[TicketState, Deps], dict[str, Any]]

PIPELINE: list[tuple[str, NodeFn]] = [
    ("intake", intake),
    ("identify", identify),
    ("assess_asset", assess_asset),
    ("diagnose", diagnose),
    ("prioritize", prioritize),
    ("plan_parts", plan_parts_node),
    ("procure", procure),
    ("schedule", schedule),
    ("finalize", finalize),
]


def _instrument(name: str, fn: NodeFn, deps: Deps, recorder: StepRecorder) -> Callable[[TicketState], dict]:
    """Run a node, turn expected failures into needs_human, and log one 'node' step."""

    def run(state: TicketState) -> dict[str, Any]:
        recorder.node = name
        started = time.perf_counter()
        error = None
        try:
            update = fn(state, deps)
        except (NodeFailure, LLMError) as exc:
            error = f"{exc.__class__.__name__}: {exc}"
            update = fail(state, f"{name}: {exc}")
        if update.get("status") == "needs_human" and error is None:
            error = update["errors"][-1]
        recorder.record(
            kind="node",
            tool_name=None,
            output=jsonable({k: v for k, v in update.items() if k != "errors"}),
            ok=error is None,
            latency_ms=round((time.perf_counter() - started) * 1000),
            error=error,
        )
        return update

    return run


def _route(next_node: str) -> Callable[[TicketState], str]:
    def route(state: TicketState) -> str:
        if state["status"] == "needs_human":
            return "escalate"
        return next_node if state["status"] == "running" else END

    return route


def build_graph(deps: Deps, recorder: StepRecorder):
    graph = StateGraph(TicketState)
    for name, fn in PIPELINE:
        graph.add_node(name, _instrument(name, fn, deps, recorder))
    graph.add_node("escalate", _instrument("escalate", escalate, deps, recorder))

    graph.add_edge(START, PIPELINE[0][0])
    names = [name for name, _ in PIPELINE]
    for current, following in zip(names, [*names[1:], END], strict=True):
        graph.add_conditional_edges(current, _route(following), [following, "escalate", END])
    graph.add_edge("escalate", END)
    return graph.compile()


def build_summary(state: TicketState) -> dict[str, Any]:
    """Compact outcome of a run, for the API, dashboard and evals."""
    diagnosis = state.get("diagnosis") or {}
    po = state.get("purchase_order") or {}
    work_order = state.get("work_order") or {}
    approval = state.get("approval") or {}
    return {
        "status": state.get("status"),
        "ticket_id": state.get("ticket_id"),
        "customer_id": (state.get("customer") or {}).get("id"),
        "asset_id": (state.get("asset") or {}).get("id"),
        "fault_code": diagnosis.get("fault_code"),
        "confidence": diagnosis.get("confidence"),
        "inspection": diagnosis.get("inspection"),
        "priority": state.get("priority"),
        "sla_deadline": state.get("sla_deadline"),
        "parts": [{"sku": p["sku"], "status": p["status"]} for p in state.get("parts_plan", [])],
        "po_created": bool(po),
        "po_id": po.get("id"),
        "po_total_lkr": po.get("total_lkr"),
        "approval_required": bool(approval.get("required")),
        "work_order_id": work_order.get("id"),
        "technician_id": work_order.get("technician_id"),
        "scheduled_start": work_order.get("scheduled_start"),
        "sla_risk": bool(work_order.get("sla_risk") or po.get("sla_risk")),
        "customer_message": state.get("customer_message"),
        "errors": state.get("errors", []),
    }


class Agent:
    """Creates runs, executes the graph and records the outcome."""

    def __init__(
        self,
        store: RunStore,
        *,
        provider: LLMProvider | None = None,
        provider_factory: Callable[[], LLMProvider] = provider_from_env,
        tools_factory: Callable[[Callable[[], datetime]], Any] | None = None,
        rules: RulesConfig | None = None,
    ) -> None:
        self.store = store
        self.rules = rules or load_rules()
        self._provider = provider
        self._provider_factory = provider_factory
        if tools_factory is None:
            client = EnterpriseClient()
            tools_factory = lambda clock: EnterpriseTools(client, self.rules, clock)  # noqa: E731
        self._tools_factory = tools_factory

    def start(self, customer_email: str, text: str, now: datetime | None = None) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        self.store.create_run(run_id, customer_email, text, now or agent_now())
        return run_id

    def execute(self, run_id: str, on_step: Callable[[StepRecord], None] | None = None) -> TicketState:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        clock = run.clock
        recorder = StepRecorder(run_id, self.store, on_step)
        state = initial_state(run_id, clock.isoformat(), run.customer_email, run.raw_text)
        try:
            provider = self._provider or self._provider_factory()
            deps = Deps(
                tools=LoggedTools(self._tools_factory(lambda: clock), recorder),
                llm=StructuredLLM(provider, on_call=recorder.llm_call),
                rules=self.rules,
            )
            state = build_graph(deps, recorder).invoke(state)
            error = None
        except Exception as exc:  # config problems or bugs: never leave a run stuck in "running"
            error = f"{exc.__class__.__name__}: {exc}"
            state = {**state, **fail(state, error)}
        self.store.update_run(
            run_id,
            status=state["status"],
            ticket_id=state.get("ticket_id"),
            summary=jsonable(build_summary(state)),
            final_state=jsonable(state),
            error=error
            or ((state.get("errors") or [None])[-1] if state["status"] == "needs_human" else None),
            input_tokens=recorder.input_tokens,
            output_tokens=recorder.output_tokens,
            finished_at=now_lk(),
        )
        return state

    def run(
        self,
        customer_email: str,
        text: str,
        now: datetime | None = None,
        on_step: Callable[[StepRecord], None] | None = None,
    ) -> TicketState:
        return self.execute(self.start(customer_email, text, now), on_step)
