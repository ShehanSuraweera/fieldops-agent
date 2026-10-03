"""Unit tests for the eval runner: scenario files, comparison, the run loop and reports. No LLM."""

import json
from collections import Counter

import httpx
import pytest

import run_evals
from run_evals import (
    GROUPS,
    EvalClient,
    Result,
    compare,
    load_scenarios,
    render_markdown,
    run_scenario,
    summarize,
)


def test_scenario_files_match_the_spec() -> None:
    scenarios = load_scenarios()
    assert len(scenarios) == 30
    assert Counter(s.group for s in scenarios) == Counter(GROUPS)
    for s in scenarios:
        assert s.expect.get("final_status"), s.id
        paused = s.group == "po_over_threshold" or s.expect.get("approval_required") is True
        assert (s.approval_decision is not None) == paused, s.id
    demo = next(s for s in scenarios if s.id.startswith("demo-freshmart"))
    assert demo.expect["technician_id"] == "TECH-02"


def test_unknown_expect_field_is_rejected(tmp_path) -> None:
    (tmp_path / "x.yaml").write_text(
        "id: x\ngroup: in_stock\nticket: {customer_email: a@b.lk, text: hi}\nexpect: {colour: red}\n"
    )
    with pytest.raises(ValueError, match="unknown expect fields"):
        load_scenarios(tmp_path)


def test_compare_maps_final_status_and_checks_each_field() -> None:
    checks = compare(
        {"final_status": "scheduled", "technician_id": "TECH-02", "po_created": False},
        {"status": "scheduled", "technician_id": "TECH-01", "po_created": False},
    )
    assert checks["final_status"]["ok"] and checks["po_created"]["ok"]
    assert checks["technician_id"] == {"expected": "TECH-02", "actual": "TECH-01", "ok": False}


class FakeServices:
    """Mock /admin/reseed and the agent API; the run pauses once if approval is needed."""

    def __init__(self, needs_approval: bool) -> None:
        self.needs_approval = needs_approval
        self.calls: list[str] = []
        self.status = "running"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        path = request.url.path
        if path == "/admin/reseed":
            assert json.loads(request.content) == {"today": run_evals.SEED_DATE}
            return httpx.Response(200, json={"seeded_for": run_evals.SEED_DATE})
        if path == "/runs" and request.method == "POST":
            assert json.loads(request.content)["now"] == run_evals.RUN_CLOCK
            self.status = "awaiting_approval" if self.needs_approval else "scheduled"
            return httpx.Response(202, json={"run_id": "run_1", "status": "running"})
        if path == "/runs/run_1/approval":
            self.status = "scheduled"
            return httpx.Response(202, json={"run_id": "run_1", "status": "running"})
        if path == "/runs/run_1":
            summary = {"status": self.status, "technician_id": "TECH-04", "po_created": True}
            steps = [
                {"kind": "tool", "tool_name": "find_customer", "ok": True, "error": None},
                {
                    "kind": "tool",
                    "tool_name": "reserve_part",
                    "ok": False,
                    "error": "insufficient_stock: none",
                },
                {"kind": "llm", "tool_name": "intake", "ok": True, "error": None},
            ]
            return httpx.Response(
                200,
                json={"status": self.status, "summary": summary, "steps": steps, "input_tokens": 900,
                      "output_tokens": 100, "error": None},
            )  # fmt: skip
        return httpx.Response(404)

    def client(self) -> EvalClient:
        client = EvalClient("http://agent", "k", "http://mock", "k", timeout_s=5)
        transport = httpx.MockTransport(self.handle)
        client.agent = httpx.Client(base_url="http://agent", transport=transport)
        client.mock = httpx.Client(base_url="http://mock", transport=transport)
        return client


def _scenario(decision: str | None):
    return run_evals.Scenario(
        id="s1", group="po_over_threshold", customer_email="a@b.lk", text="x",
        expect={"final_status": "scheduled", "technician_id": "TECH-04", "po_created": True},
        approval_decision=decision, path="s1.yaml",
    )  # fmt: skip


def test_run_scenario_answers_the_approval_pause() -> None:
    services = FakeServices(needs_approval=True)
    result = run_scenario(services.client(), _scenario("approve"))
    assert result.passed, result
    assert "POST /runs/run_1/approval" in services.calls
    assert services.calls[0] == "POST /admin/reseed"
    assert result.tokens == 1000
    assert result.tool_calls == 2
    assert result.tool_errors == ["reserve_part: insufficient_stock: none"]


def test_unanswered_pause_fails_the_scenario() -> None:
    result = run_scenario(FakeServices(needs_approval=True).client(), _scenario(None))
    assert not result.passed
    assert result.checks["final_status"]["actual"] == "awaiting_approval"


def test_summary_and_markdown_report() -> None:
    ok = Result(
        id="a",
        group="in_stock",
        passed=True,
        status="scheduled",
        latency_s=4.0,
        tokens=1000,
        tool_calls=10,
        checks={"priority": {"expected": "P1", "actual": "P1", "ok": True}},
    )
    bad = Result(id="b", group="in_stock", passed=False, status="scheduled", latency_s=6.0, tokens=2000,
                 tool_calls=10, tool_errors=["x: y"],
                 checks={"priority": {"expected": "P1", "actual": "P2", "ok": False},
                         "po_created": {"expected": False, "actual": False, "ok": True}})  # fmt: skip
    report = summarize([ok, bad], {"started_at": "2026-10-03T10:00:00Z", "seed_date": "2026-10-01"})
    assert report["overall_pass_rate"] == 0.5
    assert report["field_accuracy"] == round(2 / 3, 4)
    assert report["per_field"]["priority"] == {"checked": 2, "passed": 1, "pass_rate": 0.5}
    assert report["tool_call_errors"] == 1
    assert report["avg_latency_s"] == 5.0
    assert report["avg_tokens_per_run"] == 1500
    markdown = render_markdown(report)
    assert "**Overall pass rate: 50.0%**" in markdown
    assert "`priority`: expected `P1`, got `P2`" in markdown
