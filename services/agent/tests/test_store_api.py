"""Postgres run store and the agent HTTP API."""

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
