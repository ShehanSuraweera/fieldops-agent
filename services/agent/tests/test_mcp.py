"""TOOL_TRANSPORT=mcp: the MCP server's tools, the McpTools client, and the whole graph over MCP."""

import socket
import threading
import time
from collections.abc import Iterator
from datetime import timedelta

import anyio
import httpx
import pytest
import uvicorn
from mcp import Client
from mcp.server.transport_security import TransportSecuritySettings

from app.graph import Agent, build_summary
from app.mcp_server import build_server, with_api_key
from app.mcp_tools import McpConnection, McpTools, http_client_factory
from app.store import MemoryRunStore
from tests.fakes import NOW, RULES, FakeEnterprise, FakeLLM, diagnosis_reply, intake_reply

TOOL_NAMES = {
    "find_customer", "create_ticket", "update_ticket", "send_customer_message",
    "find_asset", "get_asset_summary", "list_fault_codes", "find_available_technicians", "create_work_order",
    "check_part_stock", "reserve_part", "find_vendors", "create_purchase_order", "update_purchase_order",
}  # fmt: skip


@pytest.fixture
def enterprise() -> FakeEnterprise:
    return FakeEnterprise()


@pytest.fixture
def connection(enterprise: FakeEnterprise) -> Iterator[McpConnection]:
    server = build_server(enterprise)
    conn = McpConnection(lambda: Client(server, cache=None))
    yield conn
    conn.close()


# --- the server ------------------------------------------------------------------


def test_server_exposes_the_agent_tool_set(enterprise: FakeEnterprise) -> None:
    async def list_tools():
        async with Client(build_server(enterprise), cache=None) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in anyio.run(list_tools)}
    assert set(tools) == TOOL_NAMES
    assert "as_of" in tools["get_asset_summary"].input_schema["properties"]
    assert all(tool.description for tool in tools.values())


def test_tool_results_keep_the_ok_data_error_shape(enterprise: FakeEnterprise) -> None:
    async def calls():
        async with Client(build_server(enterprise), cache=None) as client:
            found = await client.call_tool("find_customer", {"email": "ops@freshmart.lk"})
            missing = await client.call_tool("find_customer", {"email": "nobody@x.lk"})
            bad = await client.call_tool("reserve_part", {"sku": "CMP-AP", "qty": "lots", "ticket_id": "T"})
            return found, missing, bad

    found, missing, bad = anyio.run(calls)
    assert found.structured_content["ok"] is True
    assert found.structured_content["data"]["id"] == "CUST-001"
    assert missing.structured_content["ok"] is False
    assert missing.structured_content["error"]["code"] == "not_found"
    assert bad.is_error  # argument validation happens in the MCP server


# --- the client ------------------------------------------------------------------


def test_mcp_tools_return_the_same_typed_results(
    connection: McpConnection, enterprise: FakeEnterprise
) -> None:
    tools = McpTools(connection, clock=lambda: NOW)
    customer = tools.find_customer("ops@freshmart.lk")
    assert customer.ok and customer.data.name == "FreshMart"
    assets = tools.find_asset("CUST-001", "")  # empty query = all of the customer's assets
    assert {a.id for a in assets.data} == {"FRZ-1041", "FRZ-1043", "CHL-1044"}
    summary = tools.get_asset_summary("FRZ-1043")
    assert summary.data.facts.repeat_failure_90d
    assert enterprise.last_as_of == NOW.date()  # facts use the run's date, not the server's clock
    missing = tools.check_part_stock("NOPE")
    assert not missing.ok and missing.error.code == "not_found"
    invalid = tools.update_ticket("TKT-000001", {"priorty": "P1"})
    assert invalid.error.code == "invalid_input"  # validated before anything is sent


def test_unreachable_server_is_a_failed_tool_result() -> None:
    def broken() -> Client:
        raise ConnectionError("connection refused")

    connection = McpConnection(broken, call_timeout_s=5)
    result = McpTools(connection).find_customer("ops@freshmart.lk")
    assert not result.ok
    assert result.error.code == "mcp_unavailable"
    connection.close()


# --- the whole graph over MCP ----------------------------------------------------


def _agent(connection: McpConnection, script: dict) -> tuple[Agent, MemoryRunStore]:
    store = MemoryRunStore()
    agent = Agent(
        store, provider=FakeLLM(script), rules=RULES, tools_factory=lambda clock: McpTools(connection, clock)
    )
    return agent, store


def test_freshmart_demo_over_mcp(enterprise: FakeEnterprise, connection: McpConnection) -> None:
    agent, store = _agent(
        connection, {"intake": intake_reply("Freezer #3", "Colombo 7"), "diagnose": diagnosis_reply()}
    )
    state = agent.run("ops@freshmart.lk", "Freezer #3 at our Colombo 7 branch stopped cooling.", now=NOW)
    summary = build_summary(state)
    assert summary["status"] == "scheduled", summary["errors"]
    assert summary["technician_id"] == "TECH-02"
    assert summary["parts"] == [{"sku": "CMP-AP", "status": "reserved"}]
    assert enterprise.tickets[summary["ticket_id"]].status == "scheduled"
    tool_steps = [s for s in store.steps if s.kind == "tool"]
    assert tool_steps and all(s.ok for s in tool_steps)


def test_approval_pause_and_resume_over_mcp(enterprise: FakeEnterprise, connection: McpConnection) -> None:
    agent, _ = _agent(
        connection, {"intake": intake_reply("Freezer #1", "Peradeniya"), "diagnose": diagnosis_reply()}
    )
    paused = agent.run("info@peradeniyafresh.lk", "Freezer #1 at Peradeniya stopped cooling", now=NOW)
    assert paused["status"] == "awaiting_approval", paused["errors"]
    state = agent.resume(paused["run_id"], "approve", now=NOW)
    assert state["status"] == "scheduled"
    assert enterprise.purchase_orders[state["purchase_order"]["id"]].status == "sent"
    assert len(enterprise.purchase_orders) == 1


def test_unknown_customer_over_mcp(connection: McpConnection) -> None:
    agent, _ = _agent(connection, {})
    state = agent.run("stranger@example.com", "Our freezer is broken", now=NOW)
    assert state["status"] == "needs_human"
    assert "No customer matches" in state["errors"][0]


# --- the real Streamable HTTP transport with the API key ---------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def http_server(enterprise: FakeEnterprise) -> Iterator[str]:
    security = TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*"], allowed_origins=["http://127.0.0.1:*"]
    )
    app = with_api_key(build_server(enterprise).streamable_http_app(transport_security=security), "test-key")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_streamable_http_with_api_key(http_server: str) -> None:
    assert httpx.get(f"{http_server}/health").json() == {"status": "ok"}
    unauthorized = httpx.post(f"{http_server}/mcp", json={})
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "unauthorized"

    connection = McpConnection(http_client_factory(f"{http_server}/mcp", "test-key"))
    try:
        tools = McpTools(connection, clock=lambda: NOW)
        assert tools.find_customer("ops@freshmart.lk").data.id == "CUST-001"
        slots = tools.find_available_technicians("Colombo", "refrigeration", NOW, NOW + timedelta(days=1))
        assert {t.technician_id for t in slots.data} == {"TECH-01", "TECH-02"}
    finally:
        connection.close()

    wrong_key = McpConnection(http_client_factory(f"{http_server}/mcp", "wrong"), call_timeout_s=10)
    assert McpTools(wrong_key).find_customer("ops@freshmart.lk").error.code == "mcp_unavailable"
    wrong_key.close()
