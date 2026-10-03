"""Postgres run store and the agent HTTP API."""

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import app, get_agent
from app.config import settings
from app.graph import Agent
from app.store import SqlRunStore
from tests.fakes import NOW, RULES, FakeEnterprise, FakeLLM, diagnosis_reply, intake_reply

DEMO = {"customer_email": "ops@freshmart.lk", "text": "Freezer #3 at our Colombo 7 branch stopped cooling."}


@pytest.fixture
def store(agent_db: str) -> SqlRunStore:
    return SqlRunStore(agent_db)


@pytest.fixture
def agent(store: SqlRunStore) -> Agent:
    llm = FakeLLM({"intake": intake_reply("Freezer #3", "Colombo 7"), "diagnose": diagnosis_reply()})
    enterprise = FakeEnterprise()
    return Agent(store, provider=llm, tools_factory=lambda clock: enterprise, rules=RULES)


@pytest.fixture
def client(agent: Agent) -> Iterator[TestClient]:
    app.dependency_overrides[get_agent] = lambda: agent
    with TestClient(app, headers={"X-API-Key": settings.api_key}) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_runs_and_steps_are_persisted(agent: Agent, store: SqlRunStore) -> None:
    state = agent.run(DEMO["customer_email"], DEMO["text"], now=NOW)
    run = store.get_run(state["run_id"])
    assert run.status == "scheduled"
    assert run.ticket_id == state["ticket_id"]
    assert run.clock == NOW
    assert run.summary["technician_id"] == "TECH-02"
    assert run.final_state["work_order"]["technician_id"] == "TECH-02"
    assert run.finished_at is not None
    assert (run.input_tokens, run.output_tokens) == (300, 60)

    steps = store.list_steps(run.id)
    assert [s.seq for s in steps] == list(range(1, len(steps) + 1))
    tool = next(s for s in steps if s.tool_name == "create_work_order")
    assert tool.kind == "tool" and tool.ok
    assert tool.input["technician_id"] == "TECH-02"
    assert tool.output["id"].startswith("WO-")
    llm = next(s for s in steps if s.kind == "llm")
    assert llm.input_tokens == 100 and llm.input["system"]
    assert run.id in [r.id for r in store.list_runs()]


def test_api_requires_key(agent: Agent) -> None:
    app.dependency_overrides[get_agent] = lambda: agent
    with TestClient(app) as anon:
        response = anon.get("/runs")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
        assert anon.get("/health").json() == {"status": "ok"}
    app.dependency_overrides.clear()


def test_api_starts_run_and_reports_result(client: TestClient) -> None:
    response = client.post("/runs", json={**DEMO, "now": NOW.isoformat()})
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    # TestClient runs background tasks before returning, so the run is finished here.
    detail = client.get(f"/runs/{run_id}").json()
    assert detail["status"] == "scheduled"
    assert detail["summary"]["technician_id"] == "TECH-02"
    assert detail["steps"][0]["tool_name"] == "find_customer"
    assert {s["kind"] for s in detail["steps"]} == {"node", "tool", "llm"}

    listed = client.get("/runs").json()
    assert listed[0]["id"] == run_id
    assert listed[0]["final_state"] is None  # kept out of the list view


def test_api_errors(client: TestClient) -> None:
    missing = client.get("/runs/run_nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    invalid = client.post("/runs", json={"customer_email": "x@y.lk"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"


# --- Phase 4: approvals, restart survival, SSE ------------------------------------

OVER_THRESHOLD = {
    "customer_email": "info@peradeniyafresh.lk",
    "text": "Freezer #1 at Peradeniya stopped cooling",
}


def _paused_agent(store: SqlRunStore, checkpointer) -> tuple[Agent, FakeEnterprise]:
    llm = FakeLLM({"intake": intake_reply("Freezer #1", "Peradeniya"), "diagnose": diagnosis_reply()})
    enterprise = FakeEnterprise()
    agent = Agent(
        store, provider=llm, tools_factory=lambda clock: enterprise, rules=RULES, checkpointer=checkpointer
    )
    return agent, enterprise


def test_paused_run_survives_a_restart(agent_db: str) -> None:
    """Pause with one Agent and Postgres connection pool, resume with a brand-new one."""
    from app.checkpoint import close_checkpointer, postgres_checkpointer

    saver = postgres_checkpointer(agent_db, max_size=2)
    before, enterprise = _paused_agent(SqlRunStore(agent_db), saver)
    run_id = before.run(OVER_THRESHOLD["customer_email"], OVER_THRESHOLD["text"], now=NOW)["run_id"]
    assert before.store.get_run(run_id).status == "awaiting_approval"
    close_checkpointer(saver)  # the "process" is gone
    del before

    fresh_saver = postgres_checkpointer(agent_db, max_size=2)
    try:
        after = Agent(
            SqlRunStore(agent_db),
            provider=FakeLLM({}),
            tools_factory=lambda clock: enterprise,
            rules=RULES,
            checkpointer=fresh_saver,
        )
        state = after.resume(run_id, "approve", "approved after restart", now=NOW)
    finally:
        close_checkpointer(fresh_saver)
    assert state["status"] == "scheduled", state["errors"]
    assert state["purchase_order"]["status"] == "sent"
    assert after.store.get_run(run_id).status == "scheduled"


def test_api_pending_approvals_and_decision(store: SqlRunStore) -> None:
    agent, enterprise = _paused_agent(store, None)
    app.dependency_overrides[get_agent] = lambda: agent
    try:
        with TestClient(app, headers={"X-API-Key": settings.api_key}) as client:
            run_id = client.post("/runs", json={**OVER_THRESHOLD, "now": NOW.isoformat()}).json()["run_id"]
            assert client.get(f"/runs/{run_id}").json()["status"] == "awaiting_approval"

            pending = {p["run_id"]: p for p in client.get("/approvals/pending").json()}
            item = pending[run_id]
            assert item["purchase_order"]["total_lkr"] == 129_000
            assert item["purchase_order"]["vendor_id"] == "VEN-01"
            assert "reason" in item["purchase_order"]
            assert item["diagnosis"]["fault_code"] == "COMP_FAIL"
            assert item["customer"]["name"] == "Peradeniya Fresh"

            bad = client.post(f"/runs/{run_id}/approval", json={"decision": "maybe"})
            assert bad.status_code == 422
            response = client.post(f"/runs/{run_id}/approval", json={"decision": "approve", "comment": "ok"})
            assert response.status_code == 202
            assert client.get(f"/runs/{run_id}").json()["status"] == "scheduled"
            assert run_id not in {p["run_id"] for p in client.get("/approvals/pending").json()}

            again = client.post(f"/runs/{run_id}/approval", json={"decision": "reject"})
            assert again.status_code == 409
            assert again.json()["error"]["code"] == "not_awaiting_approval"
            assert client.post("/runs/run_missing/approval", json={"decision": "approve"}).status_code == 404
    finally:
        app.dependency_overrides.clear()
    assert len(enterprise.work_orders) == 1


def _parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":")
        )
        if "event" in fields:
            events.append(
                {"event": fields["event"], "id": fields.get("id"), "data": json.loads(fields["data"])}
            )
    return events


def test_sse_streams_steps_then_status(client: TestClient) -> None:
    run_id = client.post("/runs", json={**DEMO, "now": NOW.isoformat()}).json()["run_id"]
    with client.stream("GET", f"/runs/{run_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(response.read().decode())
    steps = [e for e in events if e["event"] == "step"]
    assert [int(e["id"]) for e in steps] == list(range(1, len(steps) + 1))
    assert steps[0]["data"]["tool_name"] == "find_customer"
    assert events[-1]["event"] == "status"
    assert events[-1]["data"]["status"] == "scheduled"

    # Reconnecting with Last-Event-ID only replays what came after it.
    with client.stream(
        "GET", f"/runs/{run_id}/events", headers={"Last-Event-ID": str(len(steps) - 2)}
    ) as response:
        tail = _parse_sse(response.read().decode())
    assert [int(e["id"]) for e in tail if e["event"] == "step"] == [len(steps) - 1, len(steps)]
    assert client.get("/runs/run_missing/events").status_code == 404
