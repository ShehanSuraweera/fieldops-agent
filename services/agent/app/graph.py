"""The LangGraph workflow and the Agent that runs it.

intake -> identify -> assess_asset -> diagnose -> prioritize -> plan_parts -> procure
       -> [approve_po] -> schedule -> finalize

After every node the status decides the route: "running" continues, "needs_human"
goes to escalate, "awaiting_approval" (only after procure) goes to approve_po, which
pauses the run with interrupt() until a manager decides. Anything else ends the run.
State is checkpointed per run (thread_id = run_id), so a paused run can be resumed by
a different process, including after a restart when the checkpointer is Postgres.
"""

import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.config import agent_now, now_lk, settings
from app.llm import LLMError, LLMProvider, StructuredLLM, provider_from_env
from app.nodes.assess import assess_asset
from app.nodes.common import Deps, NodeFailure, fail
from app.nodes.diagnose import diagnose
from app.nodes.finalize import escalate, finalize
from app.nodes.identify import identify
from app.nodes.intake import intake
from app.nodes.parts import approve_po, plan_parts_node, procure
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
TERMINAL = {"needs_info", "scheduled", "needs_manual_procurement", "needs_human"}


class RunNotPaused(Exception):
    """An approval arrived for a run that is not awaiting one."""


def _instrument(name: str, fn: NodeFn, deps: Deps, recorder: StepRecorder) -> Callable[[TicketState], dict]:
    """Run a node, turn expected failures into needs_human, and log one 'node' step."""

    def run(state: TicketState) -> dict[str, Any]:
        recorder.node = name
        started = time.perf_counter()
        error = None
        try:
            update = fn(state, deps)
        except GraphInterrupt:
            recorder.record(
                kind="node",
                output={"paused": "waiting for manager approval"},
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
            raise
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
        status = state["status"]
        if status == "needs_human":
            return "escalate"
        if status == "awaiting_approval":
            return "approve_po"
        return next_node if status == "running" else END

    return route


def build_graph(deps: Deps, recorder: StepRecorder, checkpointer: BaseCheckpointSaver):
    graph = StateGraph(TicketState)
    for name, fn in PIPELINE:
        graph.add_node(name, _instrument(name, fn, deps, recorder))
    graph.add_node("approve_po", _instrument("approve_po", approve_po, deps, recorder))
    graph.add_node("escalate", _instrument("escalate", escalate, deps, recorder))

    graph.add_edge(START, PIPELINE[0][0])
    names = [name for name, _ in PIPELINE]
    for current, following in zip(names, [*names[1:], END], strict=True):
        targets = [following, "escalate", END] + (["approve_po"] if current == "procure" else [])
        graph.add_conditional_edges(current, _route(following), targets)
    graph.add_conditional_edges("approve_po", _route("schedule"), ["schedule", "escalate", END])
    graph.add_edge("escalate", END)
    return graph.compile(checkpointer=checkpointer)


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
        "po_status": po.get("status"),
        "po_total_lkr": po.get("total_lkr"),
        "warranty_claim": po.get("warranty_claim"),
        "approval_required": bool(approval.get("required")),
        "approval_decision": approval.get("decision"),
        "work_order_id": work_order.get("id"),
        "technician_id": work_order.get("technician_id"),
        "scheduled_start": work_order.get("scheduled_start"),
        # A rejected PO's lead time no longer matters; only the booked visit can be late.
        "sla_risk": bool(
            work_order.get("sla_risk") or (po.get("sla_risk") and po.get("status") != "rejected")
        ),
        "customer_message": state.get("customer_message"),
        "errors": state.get("errors", []),
    }


def default_tools_factory(transport: str, rules: RulesConfig) -> Callable[[Callable[[], datetime]], Any]:
    """TOOL_TRANSPORT=rest calls the mock APIs over HTTP; mcp calls the same tools on the MCP server."""
    if transport == "mcp":
        from app.mcp_tools import McpConnection, McpTools, http_client_factory

        connection = McpConnection(http_client_factory())
        return lambda clock: McpTools(connection, clock)
    if transport != "rest":
        raise ValueError(f"TOOL_TRANSPORT must be 'rest' or 'mcp', got {transport!r}")
    client = EnterpriseClient()
    return lambda clock: EnterpriseTools(client, rules, clock)


class Agent:
    """Creates runs, executes the graph, pauses for approval, resumes and records outcomes."""

    def __init__(
        self,
        store: RunStore,
        *,
        provider: LLMProvider | None = None,
        provider_factory: Callable[[], LLMProvider] = provider_from_env,
        tools_factory: Callable[[Callable[[], datetime]], Any] | None = None,
        rules: RulesConfig | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self.store = store
        self.rules = rules or load_rules()
        self.checkpointer = checkpointer or InMemorySaver()
        self._provider = provider
        self._provider_factory = provider_factory
        self.transport = "custom" if tools_factory else settings.tool_transport
        if tools_factory is None:
            tools_factory = default_tools_factory(settings.tool_transport, self.rules)
        self._tools_factory = tools_factory

    def start(self, customer_email: str, text: str, now: datetime | None = None) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        self.store.create_run(run_id, customer_email, text, now or agent_now())
        return run_id

    def execute(self, run_id: str, on_step: Callable[[StepRecord], None] | None = None) -> TicketState:
        """Run a new run until it finishes or pauses for approval."""
        run = self._get(run_id)
        state = initial_state(run_id, run.clock.isoformat(), run.customer_email, run.raw_text)
        return self._drive(run_id, state, on_step)

    def resume(
        self,
        run_id: str,
        decision: Literal["approve", "reject"],
        comment: str | None = None,
        now: datetime | None = None,
        on_step: Callable[[StepRecord], None] | None = None,
    ) -> TicketState:
        """Apply a manager's decision to a paused run and continue it."""
        self.claim(run_id)
        return self.continue_claimed(run_id, decision, comment, now, on_step)

    def claim(self, run_id: str) -> None:
        """Reserve a paused run for resumption; raises RunNotPaused if it is not awaiting approval."""
        self._get(run_id)
        if not self.store.claim_paused(run_id):
            raise RunNotPaused(run_id)

    def continue_claimed(
        self,
        run_id: str,
        decision: Literal["approve", "reject"],
        comment: str | None = None,
        now: datetime | None = None,
        on_step: Callable[[StepRecord], None] | None = None,
    ) -> TicketState:
        run = self._get(run_id)
        decided_at = max(now or agent_now(), run.clock)  # never before the run itself
        answer = {"decision": decision, "comment": comment, "decided_at": decided_at.isoformat()}
        return self._drive(run_id, Command(resume=answer), on_step)

    def _get(self, run_id: str):
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def _drive(
        self, run_id: str, graph_input: Any, on_step: Callable[[StepRecord], None] | None
    ) -> TicketState:
        run = self._get(run_id)
        clock = run.clock
        config = {"configurable": {"thread_id": run_id}}
        recorder = StepRecorder(run_id, self.store, on_step, start_seq=self.store.last_seq(run_id))
        error = None
        graph = None
        try:
            provider = self._provider or self._provider_factory()
            deps = Deps(
                tools=LoggedTools(self._tools_factory(lambda: clock), recorder),
                llm=StructuredLLM(provider, on_call=recorder.llm_call),
                rules=self.rules,
            )
            graph = build_graph(deps, recorder, self.checkpointer)
            graph.invoke(graph_input, config)
            snapshot = graph.get_state(config)
            state: TicketState = dict(snapshot.values)
            if snapshot.next:  # stopped at interrupt(): waiting for a manager
                state["status"] = "awaiting_approval"
        except Exception as exc:  # config problems or bugs: never leave a run stuck in "running"
            error = f"{exc.__class__.__name__}: {exc}"
            base = dict(graph.get_state(config).values) if graph is not None else {}
            if not base:
                base = initial_state(run_id, clock.isoformat(), run.customer_email, run.raw_text)
            state = {**base, **fail(base, error)}

        status = state["status"]
        if status == "needs_human" and error is None:
            error = (state.get("errors") or [None])[-1]
        self.store.update_run(
            run_id,
            status=status,
            ticket_id=state.get("ticket_id"),
            summary=jsonable(build_summary(state)),
            final_state=jsonable(state),
            error=error,
            input_tokens=run.input_tokens + recorder.input_tokens,
            output_tokens=run.output_tokens + recorder.output_tokens,
            finished_at=now_lk() if status in TERMINAL else None,
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
