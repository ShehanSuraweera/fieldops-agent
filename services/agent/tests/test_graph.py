"""End-to-end graph behaviour against the in-memory enterprise and a scripted LLM."""

import re
from datetime import datetime, timedelta

import pytest

from app.config import LK_TZ
from app.graph import Agent, build_summary
from app.store import MemoryRunStore
from tests.fakes import NOW, RULES, FakeEnterprise, FakeLLM, _err, diagnosis_reply, intake_reply

FRESHMART = "ops@freshmart.lk"
PERADENIYA = "info@peradeniyafresh.lk"
DEMO_TEXT = "Freezer #3 at our Colombo 7 branch stopped cooling again this morning."


class Harness:
    def __init__(self, script: dict) -> None:
        self.enterprise = FakeEnterprise()
        self.llm = FakeLLM(script)
        self.store = MemoryRunStore()
        self.agent = Agent(
            self.store, provider=self.llm, tools_factory=lambda clock: self.enterprise, rules=RULES
        )

    def run(self, email: str, text: str):
        state = self.agent.run(email, text, now=NOW)
        return state, build_summary(state)

    def steps(self, kind: str | None = None):
        return [s for s in self.store.steps if kind is None or s.kind == kind]

    def ticket(self, ticket_id: str):
        return self.enterprise.tickets[ticket_id]


def test_freshmart_demo_books_tech02_with_reserved_part() -> None:
    h = Harness({"intake": intake_reply("Freezer #3", "Colombo 7"), "diagnose": diagnosis_reply()})
    state, summary = h.run(FRESHMART, DEMO_TEXT)

    assert summary["status"] == "scheduled", summary["errors"]
    assert summary["asset_id"] == "FRZ-1043"
    assert summary["fault_code"] == "COMP_FAIL"
    assert summary["priority"] == "P1"
    assert summary["po_created"] is False
    assert summary["approval_required"] is False
    assert summary["technician_id"] == "TECH-02"  # did the last repair on this asset
    assert datetime.fromisoformat(summary["scheduled_start"]) == datetime(2026, 10, 1, 10, tzinfo=LK_TZ)
    assert summary["parts"] == [{"sku": "CMP-AP", "status": "reserved"}]
    assert not summary["sla_risk"]

    ticket = h.ticket(summary["ticket_id"])
    assert ticket.status == "scheduled"
    assert ticket.priority == "P1"
    assert ticket.sla_deadline == NOW + timedelta(hours=4)
    assert "Warranty claim" in ticket.resolution_note
    assert "Supervisor review" in ticket.resolution_note
    assert "Kasun Fernando" in ticket.messages[-1].body
    assert h.enterprise.parts["CMP-AP"].stock_qty == 1


def test_every_tool_and_llm_call_is_logged() -> None:
    h = Harness({"intake": intake_reply("Freezer #3", "Colombo 7"), "diagnose": diagnosis_reply()})
    state, _ = h.run(FRESHMART, DEMO_TEXT)
    run = h.store.get_run(state["run_id"])

    nodes = [s.node for s in h.steps("node")]
    assert nodes == [
        "intake",
        "identify",
        "assess_asset",
        "diagnose",
        "prioritize",
        "plan_parts",
        "procure",
        "schedule",
        "finalize",
    ]
    tools = [s.tool_name for s in h.steps("tool")]
    assert tools[:2] == ["find_customer", "create_ticket"]
    assert "reserve_part" in tools and "create_work_order" in tools
    assert [s.tool_name for s in h.steps("llm")] == ["intake", "diagnose", "customer_message"]
    assert all(s.input_tokens == 100 and s.output_tokens == 20 for s in h.steps("llm"))
    assert [s.seq for s in h.steps()] == list(range(1, len(h.steps()) + 1))
    assert run.status == "scheduled"
    assert (run.input_tokens, run.output_tokens) == (300, 60)
    assert run.summary["technician_id"] == "TECH-02"


def test_prompts_never_see_business_rules() -> None:
    h = Harness({"intake": intake_reply("Freezer #3"), "diagnose": diagnosis_reply()})
    h.run(FRESHMART, DEMO_TEXT)
    for request in h.llm.requests:
        text = (request.system + request.user).lower()
        for leak in (r"sla", "100000", "100,000", "threshold", r"priority", r"p1", "warranty claim"):
            assert not re.search(leak, text), (request.prompt_name, leak)


def test_unknown_customer_needs_human_without_llm_or_ticket() -> None:
    h = Harness({})
    state, summary = h.run("stranger@example.com", "Our freezer is broken")
    assert summary["status"] == "needs_human"
    assert summary["ticket_id"] is None
    assert "No customer matches" in summary["errors"][0]
    assert h.llm.requests == []
    assert [s.node for s in h.steps("node")] == ["intake", "escalate"]


def test_ambiguous_asset_asks_one_clarifying_question() -> None:
    question = {"question": "Which Freezer #1 is affected: Peradeniya or Kandy City?"}
    h = Harness({"intake": intake_reply("Freezer #1"), "clarify": question})
    state, summary = h.run(PERADENIYA, "Freezer #1 is leaking water everywhere")
    assert summary["status"] == "needs_info"
    assert summary["asset_id"] is None
    ticket = h.ticket(summary["ticket_id"])
    assert ticket.status == "needs_info"
    assert [m.body for m in ticket.messages] == [question["question"]]
    assert "Peradeniya" in h.llm.calls("clarify")[0].user and "Kandy City" in h.llm.calls("clarify")[0].user
    assert h.enterprise.work_orders == {}


def test_site_hint_resolves_duplicate_names() -> None:
    h = Harness({"intake": intake_reply("Freezer #1", "Peradeniya"), "diagnose": diagnosis_reply()})
    _, summary = h.run(PERADENIYA, "Freezer #1 at Peradeniya stopped cooling")
    assert summary["asset_id"] == "FRZ-1010"


def test_out_of_stock_part_is_ordered_then_visit_follows_delivery() -> None:
    h = Harness({"intake": intake_reply("Freezer #1", "Peradeniya"), "diagnose": diagnosis_reply()})
    state, summary = h.run(PERADENIYA, "Freezer #1 at Peradeniya stopped cooling")

    assert summary["status"] == "scheduled", summary["errors"]
    assert summary["parts"] == [{"sku": "CMP-FL", "status": "needs_po"}]
    assert summary["po_created"]
    po = h.enterprise.purchase_orders[summary["po_id"]]
    # Bronze SLA is 72 h: VEN-01 (2 days) meets it, VEN-03 (5 days) is cheaper but late, VEN-05 is unapproved.
    assert po.vendor_id == "VEN-01"
    assert po.total_lkr == 129_000
    assert po.status == "sent"
    assert po.warranty_claim is False
    assert summary["approval_required"] is True  # over LKR 100,000; auto-approved until Phase 4
    assert state["approval"]["decision"] == "approve"
    start = datetime.fromisoformat(summary["scheduled_start"])
    assert start >= NOW + timedelta(days=2)
    assert summary["technician_id"] == "TECH-04"


def test_low_confidence_books_inspection_without_parts() -> None:
    h = Harness(
        {"intake": intake_reply("Freezer #3", down=False), "diagnose": diagnosis_reply("THERMO_FAIL", 0.4)}
    )
    state, summary = h.run(FRESHMART, "Freezer #3 makes a strange noise sometimes")
    assert summary["status"] == "scheduled"
    assert summary["inspection"] is True
    assert summary["parts"] == []
    assert summary["po_created"] is False
    assert state["diagnosis"]["est_hours"] == RULES.inspection_hours
    assert "Inspection visit" in h.llm.calls("customer_message")[0].user


def test_invalid_fault_code_is_retried_once_with_feedback() -> None:
    h = Harness(
        {"intake": intake_reply("Freezer #3"), "diagnose": [diagnosis_reply("MADE_UP"), diagnosis_reply()]}
    )
    _, summary = h.run(FRESHMART, DEMO_TEXT)
    assert summary["status"] == "scheduled"
    attempts = h.llm.calls("diagnose")
    assert len(attempts) == 2
    assert "previous reply was rejected" in attempts[1].user
    diagnose_rows = [s for s in h.steps("llm") if s.tool_name == "diagnose"]
    assert [s.ok for s in diagnose_rows] == [False, True]


def test_two_invalid_outputs_route_to_human_review() -> None:
    h = Harness({"intake": intake_reply("Freezer #3"), "diagnose": "this is not json"})
    _, summary = h.run(FRESHMART, DEMO_TEXT)
    assert summary["status"] == "needs_human"
    assert "failed validation twice" in summary["errors"][-1]
    ticket = h.ticket(summary["ticket_id"])
    assert ticket.status == "needs_human"
    assert "Needs human review" in ticket.resolution_note
    assert len(h.llm.calls("diagnose")) == 2


def test_message_missing_the_booking_facts_is_rejected() -> None:
    vague = {"body": "Someone will come by soon to look at your freezer. CoolTech Services"}
    h = Harness(
        {"intake": intake_reply("Freezer #3"), "diagnose": diagnosis_reply(), "customer_message": vague}
    )
    _, summary = h.run(FRESHMART, DEMO_TEXT)
    assert summary["status"] == "needs_human"
    assert "customer_message" in summary["errors"][-1]


def test_no_technician_available_needs_human() -> None:
    h = Harness({"intake": intake_reply("Freezer #3"), "diagnose": diagnosis_reply()})
    h.enterprise.slots = {}
    _, summary = h.run(FRESHMART, DEMO_TEXT)
    assert summary["status"] == "needs_human"
    assert "No technician" in summary["errors"][-1]
    assert summary["parts"] == [{"sku": "CMP-AP", "status": "reserved"}]


def test_tool_failure_routes_to_human_review() -> None:
    h = Harness({"intake": intake_reply("Freezer #3"), "diagnose": diagnosis_reply()})
    h.enterprise.list_fault_codes = lambda model: _err("timeout", "GET /fsm/fault-codes timed out", None)
    _, summary = h.run(FRESHMART, DEMO_TEXT)
    assert summary["status"] == "needs_human"
    assert "list_fault_codes failed (timeout)" in summary["errors"][-1]


def test_missing_llm_key_fails_the_run_cleanly() -> None:
    from app.llm import LLMConfigError

    def no_provider():
        raise LLMConfigError("GEMINI_API_KEY is not set")

    store = MemoryRunStore()
    agent = Agent(
        store, provider_factory=no_provider, tools_factory=lambda clock: FakeEnterprise(), rules=RULES
    )
    state = agent.run(FRESHMART, DEMO_TEXT, now=NOW)
    run = store.get_run(state["run_id"])
    assert run.status == "needs_human"
    assert "GEMINI_API_KEY" in run.error


@pytest.mark.parametrize("email", [FRESHMART, "OPS@FRESHMART.LK"])
def test_runs_are_independent(email: str) -> None:
    h = Harness({"intake": intake_reply("Freezer #3"), "diagnose": diagnosis_reply()})
    first, _ = h.run(email, DEMO_TEXT)
    second, summary = h.run(email, DEMO_TEXT)
    assert first["run_id"] != second["run_id"]
    assert build_summary(first)["technician_id"] == "TECH-02"
    # TECH-02 has no 4-hour block left inside the SLA, so the SLA wins over prior work.
    assert summary["technician_id"] == "TECH-01"
    assert not summary["sla_risk"]
