"""Human-in-the-loop approval: pause on over-threshold POs, resume on approve or reject."""

from datetime import datetime, timedelta

import pytest

from app.graph import Agent, RunNotPaused, build_summary
from app.store import MemoryRunStore
from tests.fakes import NOW, RULES, FakeEnterprise, FakeLLM, diagnosis_reply, intake_reply

PERADENIYA = "info@peradeniyafresh.lk"
OVER = "Freezer #1 at Peradeniya stopped cooling"  # CMP-FL: LKR 129,000 from VEN-01, needs approval


class Harness:
    def __init__(self, script: dict | None = None) -> None:
        self.enterprise = FakeEnterprise()
        self.llm = FakeLLM(
            script or {"intake": intake_reply("Freezer #1", "Peradeniya"), "diagnose": diagnosis_reply()}
        )
        self.store = MemoryRunStore()
        self.agent = Agent(
            self.store, provider=self.llm, tools_factory=lambda clock: self.enterprise, rules=RULES
        )

    def pause(self) -> str:
        state = self.agent.run(PERADENIYA, OVER, now=NOW)
        assert state["status"] == "awaiting_approval", state["errors"]
        return state["run_id"]


def test_approve_sends_po_and_schedules_after_delivery() -> None:
    h = Harness()
    run_id = h.pause()
    decided = NOW + timedelta(hours=3)
    state = h.agent.resume(run_id, "approve", "OK, compressor is needed", now=decided)
    summary = build_summary(state)

    assert summary["status"] == "scheduled", summary["errors"]
    po = h.enterprise.purchase_orders[summary["po_id"]]
    assert po.status == "sent"
    assert summary["approval_decision"] == "approve"
    assert state["approval"]["comment"] == "OK, compressor is needed"
    # VEN-01's 2-day lead time starts when the PO is sent, i.e. at approval.
    ready = decided + timedelta(days=2)
    assert datetime.fromisoformat(state["parts_ready_at"]) == ready
    assert datetime.fromisoformat(summary["scheduled_start"]) >= ready
    assert summary["technician_id"] == "TECH-04"
    ticket = h.enterprise.tickets[summary["ticket_id"]]
    assert ticket.status == "scheduled"
    assert "Parts ordered on PO-000001" in ticket.resolution_note
    assert "ordered and will arrive" in h.llm.calls("customer_message")[0].user

    run = h.store.get_run(run_id)
    assert run.status == "scheduled"
    assert run.finished_at is not None


def test_reject_books_inspection_and_flags_manual_procurement() -> None:
    h = Harness()
    run_id = h.pause()
    decided = NOW + timedelta(hours=2)  # 11:00, after the 10:00 slot
    state = h.agent.resume(run_id, "reject", "Use the refurbished unit in stores", now=decided)
    summary = build_summary(state)

    assert summary["status"] == "needs_manual_procurement", summary["errors"]
    assert h.enterprise.purchase_orders[summary["po_id"]].status == "rejected"
    assert summary["approval_decision"] == "reject"
    # Inspection-only visit, not waiting for parts that are not coming.
    assert state["parts_ready_at"] is None
    work_order = h.enterprise.work_orders[summary["work_order_id"]]
    assert work_order.est_hours == RULES.inspection_hours
    assert decided <= work_order.scheduled_start < NOW + timedelta(days=1)  # never before the decision
    assert summary["sla_risk"] is False  # the rejected PO's lead time no longer counts
    ticket = h.enterprise.tickets[summary["ticket_id"]]
    assert ticket.status == "needs_manual_procurement"
    assert "Manual procurement needed" in ticket.resolution_note
    assert "Use the refurbished unit" in ticket.resolution_note
    facts = h.llm.calls("customer_message")[0].user
    assert "delay in sourcing" in facts and "Inspection visit" in facts
    assert h.store.get_run(run_id).status == "needs_manual_procurement"


def test_under_threshold_po_is_approved_without_pausing() -> None:
    h = Harness(
        {"intake": intake_reply("Freezer #1", "Peradeniya"), "diagnose": diagnosis_reply("THERMO_FAIL")}
    )
    state = h.agent.run(PERADENIYA, "Freezer #1 at Peradeniya: temperature all over the place", now=NOW)
    summary = build_summary(state)
    assert summary["status"] == "scheduled", summary["errors"]
    assert summary["po_total_lkr"] == 19_800
    assert summary["approval_required"] is False
    assert summary["po_status"] == "sent"
    assert "approve_po" not in [s.node for s in h.store.steps if s.kind == "node"]


def test_steps_continue_numbering_after_resume() -> None:
    h = Harness()
    run_id = h.pause()
    before = h.store.last_seq(run_id)
    h.agent.resume(run_id, "approve", now=NOW)
    seqs = [s.seq for s in h.store.list_steps(run_id)]
    assert seqs == list(range(1, len(seqs) + 1))
    resumed_nodes = [s.node for s in h.store.list_steps(run_id, after_seq=before) if s.kind == "node"]
    assert resumed_nodes == ["approve_po", "schedule", "finalize"]


def test_tokens_accumulate_across_resume() -> None:
    h = Harness()
    run_id = h.pause()
    paused_tokens = h.store.get_run(run_id).input_tokens
    h.agent.resume(run_id, "approve", now=NOW)
    assert h.store.get_run(run_id).input_tokens == paused_tokens + 100  # one more LLM call (the message)


def test_cannot_resume_twice_or_resume_a_run_that_is_not_paused() -> None:
    h = Harness()
    run_id = h.pause()
    h.agent.resume(run_id, "approve", now=NOW)
    with pytest.raises(RunNotPaused):
        h.agent.resume(run_id, "reject", now=NOW)
    assert len(h.enterprise.work_orders) == 1

    done = h.agent.run("ops@freshmart.lk", "Freezer #3 stopped cooling", now=NOW)
    with pytest.raises(RunNotPaused):
        h.agent.resume(done["run_id"], "approve")
    with pytest.raises(KeyError):
        h.agent.resume("run_missing", "approve")


def test_resume_never_duplicates_side_effects() -> None:
    h = Harness()
    run_id = h.pause()
    h.agent.resume(run_id, "approve", now=NOW)
    assert len(h.enterprise.purchase_orders) == 1  # procure ran once; approve_po re-ran safely
    assert len(h.enterprise.tickets) == 1


def test_decision_time_is_never_before_the_run_clock() -> None:
    h = Harness()
    run_id = h.pause()
    state = h.agent.resume(run_id, "approve", now=NOW - timedelta(days=3))
    assert datetime.fromisoformat(state["approval"]["decided_at"]) == NOW


def test_a_new_agent_can_resume_using_the_same_checkpointer() -> None:
    """Restart, in miniature: the paused run lives in the store and checkpointer, not the Agent."""
    first = Harness()
    run_id = first.pause()
    second = Agent(
        first.store,
        provider=first.llm,
        tools_factory=lambda clock: first.enterprise,
        rules=RULES,
        checkpointer=first.agent.checkpointer,
    )
    state = second.resume(run_id, "approve", now=NOW)
    assert state["status"] == "scheduled"
