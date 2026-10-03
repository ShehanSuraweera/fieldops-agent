"""Tool and HTTP client tests against an in-process fake API (httpx.MockTransport)."""

import json
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
import pytest

from app.config import LK_TZ
from app.enterprise import PartNeed
from app.rules import load_rules
from app.tools import EnterpriseClient, EnterpriseTools

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=LK_TZ)

ASSET = {
    "id": "FRZ-1043",
    "customer_id": "CUST-001",
    "name": "Freezer #3",
    "model": "AP-500",
    "serial": "SN-AP-500-1043",
    "site": "Colombo 7",
    "region": "Colombo",
    "install_date": "2025-08-08",
    "warranty_end": "2027-08-08",
}
HISTORY = [
    {
        "id": 2,
        "asset_id": "FRZ-1043",
        "date": "2026-08-02",
        "fault_code": "COMP_FAIL",
        "notes": "",
        "parts_used": [{"sku": "CMP-AP", "qty": 1}],
        "technician_id": "TECH-02",
    },
    {
        "id": 1,
        "asset_id": "FRZ-1043",
        "date": "2025-12-05",
        "fault_code": "THERMO_FAIL",
        "notes": "",
        "parts_used": [],
        "technician_id": "TECH-01",
    },
]
TICKET = {
    "id": "TKT-000001",
    "customer_id": "CUST-001",
    "asset_id": "FRZ-1043",
    "description": "Freezer #3 stopped cooling",
    "status": "scheduled",
    "priority": "P1",
    "sla_deadline": "2026-10-01T13:00:00+05:30",
    "created_at": "2026-10-01T09:00:00+05:30",
    "resolution_note": None,
    "messages": [],
}


class FakeAPI:
    """Routes requests to handlers and records every call and backoff sleep."""

    def __init__(self, routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]) -> None:
        self.routes = routes
        self.calls: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        handler = self.routes.get((request.method, request.url.path))
        if handler is None:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "no route"}})
        return handler(request)

    def tools(self) -> EnterpriseTools:
        client = EnterpriseClient(
            "http://mock", "test-key", transport=httpx.MockTransport(self.handle), sleep=self.sleeps.append
        )
        return EnterpriseTools(client, load_rules(), clock=lambda: NOW)


def respond(status: int, body: object = None) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(status, json=body)


def sequence(*steps: Callable[[httpx.Request], httpx.Response]):
    """Handler that plays the given handlers in order, repeating the last one."""
    remaining = list(steps)

    def handler(request: httpx.Request) -> httpx.Response:
        step = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return step(request)

    return handler


def raise_(exc_type: type[Exception]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("boom", request=request)

    return handler


# --- client behaviour ------------------------------------------------------------


def test_client_defaults() -> None:
    client = EnterpriseClient("http://mock", "k")
    assert client.timeout == httpx.Timeout(10.0)
    assert client.max_retries == 2


def test_sends_api_key_and_returns_compact_result() -> None:
    customer = {
        "id": "CUST-001",
        "name": "FreshMart",
        "region": "Colombo",
        "contract_tier": "gold",
        "contact_email": "ops@freshmart.lk",
    }
    api = FakeAPI({("GET", "/crm/customers"): respond(200, [customer])})
    result = api.tools().find_customer("ops@freshmart.lk")
    assert result.ok and result.data.id == "CUST-001"
    assert result.compact() == {"ok": True, "data": customer}
    assert api.calls[0].headers["X-API-Key"] == "test-key"
    assert api.calls[0].url.params["email"] == "ops@freshmart.lk"


def test_get_retries_5xx_with_backoff_then_succeeds() -> None:
    api = FakeAPI({("GET", "/erp/parts/CMP-AP"): sequence(respond(503), respond(502), respond(200, _part()))})
    result = api.tools().check_part_stock("CMP-AP")
    assert result.ok
    assert len(api.calls) == 3
    assert api.sleeps == [0.5, 1.0]


def test_get_gives_up_after_two_retries() -> None:
    api = FakeAPI(
        {("GET", "/erp/parts/CMP-AP"): respond(500, {"error": {"code": "boom", "message": "down"}})}
    )
    result = api.tools().check_part_stock("CMP-AP")
    assert not result.ok
    assert len(api.calls) == 3
    assert result.error.code == "boom"
    assert result.error.status_code == 500
    assert result.error.attempts == 3
    assert result.error.retryable


def test_get_retries_timeouts() -> None:
    api = FakeAPI({("GET", "/erp/parts/CMP-AP"): sequence(raise_(httpx.ReadTimeout), respond(200, _part()))})
    assert api.tools().check_part_stock("CMP-AP").ok
    assert len(api.calls) == 2


def test_timeout_error_after_retries() -> None:
    api = FakeAPI({("GET", "/erp/parts/CMP-AP"): raise_(httpx.ReadTimeout)})
    result = api.tools().check_part_stock("CMP-AP")
    assert result.error.code == "timeout"
    assert len(api.calls) == 3


def test_4xx_is_not_retried_and_keeps_api_error_code() -> None:
    body = {"error": {"code": "insufficient_stock", "message": "Only 0 in stock", "details": {}}}
    api = FakeAPI({("POST", "/erp/reservations"): respond(409, body)})
    result = api.tools().reserve_part("CMP-FL", 1, "TKT-000001")
    assert not result.ok
    assert result.error.code == "insufficient_stock"
    assert result.error.status_code == 409
    assert not result.error.retryable
    assert len(api.calls) == 1
    assert api.sleeps == []


def test_post_is_not_retried_after_read_timeout() -> None:
    api = FakeAPI({("POST", "/erp/reservations"): raise_(httpx.ReadTimeout)})
    result = api.tools().reserve_part("CMP-AP", 1, "TKT-000001")
    assert result.error.code == "timeout"
    assert len(api.calls) == 1  # may have reached the server; never double-reserve


def test_post_is_not_retried_on_5xx() -> None:
    api = FakeAPI({("POST", "/crm/tickets/TKT-000001/messages"): respond(503)})
    result = api.tools().send_customer_message("TKT-000001", "hello")
    assert result.error.code == "http_error"
    assert len(api.calls) == 1


def test_post_is_retried_when_connection_failed() -> None:
    message = {
        "id": 1,
        "ticket_id": "TKT-000001",
        "direction": "outbound",
        "body": "hi",
        "created_at": "2026-10-01T09:00:00+05:30",
    }
    route = sequence(raise_(httpx.ConnectError), respond(201, message))
    api = FakeAPI({("POST", "/crm/tickets/TKT-000001/messages"): route})
    result = api.tools().send_customer_message("TKT-000001", "hi")
    assert result.ok
    assert len(api.calls) == 2
    assert json.loads(api.calls[-1].content) == {"body": "hi", "direction": "outbound"}


def test_invalid_json_and_schema_mismatch_are_errors() -> None:
    api = FakeAPI(
        {
            ("GET", "/erp/parts/A"): lambda _: httpx.Response(200, content=b"<html>"),
            ("GET", "/erp/parts/B"): respond(200, {"sku": "B"}),
        }
    )
    tools = api.tools()
    assert tools.check_part_stock("A").error.code == "invalid_response"
    assert tools.check_part_stock("B").error.code == "invalid_response"


# --- individual tools ----------------------------------------------------------


def _part() -> dict:
    return {
        "sku": "CMP-AP",
        "name": "Compressor",
        "compatible_models": ["AP-500"],
        "unit_cost_lkr": 92000,
        "stock_qty": 2,
        "reorder_level": 1,
    }


def test_find_customer_not_found() -> None:
    api = FakeAPI({("GET", "/crm/customers"): respond(200, [])})
    result = api.tools().find_customer("nobody@example.com")
    assert not result.ok
    assert result.error.code == "not_found"


def test_find_asset_can_return_several_candidates() -> None:
    other = {**ASSET, "id": "FRZ-1041", "name": "Freezer #1"}
    api = FakeAPI({("GET", "/fsm/assets"): respond(200, [ASSET, other])})
    result = api.tools().find_asset("CUST-001", "Freezer")
    assert [a.id for a in result.data] == ["FRZ-1043", "FRZ-1041"]
    assert dict(api.calls[0].url.params) == {"customer_id": "CUST-001", "q": "Freezer"}


def test_get_asset_summary_computes_facts_with_rules() -> None:
    api = FakeAPI(
        {
            ("GET", "/fsm/assets/FRZ-1043"): respond(200, ASSET),
            ("GET", "/fsm/assets/FRZ-1043/history"): respond(200, HISTORY),
        }
    )
    summary = api.tools().get_asset_summary("FRZ-1043").data
    assert summary.asset.id == "FRZ-1043"
    assert summary.facts.under_warranty
    assert summary.facts.repeat_failure_90d  # 60 days before NOW
    assert summary.facts.last_technician_id == "TECH-02"
    assert summary.facts.previous_technician_ids == ["TECH-02", "TECH-01"]
    assert len(summary.recent_history) == 2


def test_get_asset_summary_propagates_not_found() -> None:
    api = FakeAPI({})
    result = api.tools().get_asset_summary("NOPE")
    assert result.error.code == "not_found"
    assert len(api.calls) == 1


def test_find_vendors_maps_offers() -> None:
    vendors = [
        {
            "id": "VEN-03",
            "name": "Lanka Industrial Spares",
            "approved": True,
            "lead_time_days": 5,
            "price_lkr": 115000,
        },
        {"id": "VEN-05", "name": "QuickParts", "approved": False, "lead_time_days": 1, "price_lkr": 106000},
    ]
    api = FakeAPI({("GET", "/erp/vendors"): respond(200, vendors)})
    offers = api.tools().find_vendors("CMP-FL").data
    assert [(o.vendor_id, o.sku, o.approved, o.price_lkr) for o in offers] == [
        ("VEN-03", "CMP-FL", True, 115000),
        ("VEN-05", "CMP-FL", False, 106000),
    ]


def test_find_available_technicians_sends_iso_window() -> None:
    api = FakeAPI({("GET", "/fsm/technicians/available"): respond(200, [])})
    api.tools().find_available_technicians("Colombo", "refrigeration", NOW, NOW + timedelta(hours=4))
    params = api.calls[0].url.params
    assert params["region"] == "Colombo"
    assert params["skill"] == "refrigeration"
    assert params["from"] == "2026-10-01T09:00:00+05:30"
    assert params["to"] == "2026-10-01T13:00:00+05:30"


def test_create_work_order_payload() -> None:
    work_order = {
        "id": "WO-000001",
        "ticket_id": "TKT-000001",
        "asset_id": "FRZ-1043",
        "technician_id": "TECH-02",
        "scheduled_start": "2026-10-01T10:00:00+05:30",
        "est_hours": 4,
        "status": "scheduled",
        "created_at": "2026-10-01T09:00:00+05:30",
    }
    api = FakeAPI({("POST", "/fsm/work-orders"): respond(201, work_order)})
    start = NOW + timedelta(hours=1)
    result = api.tools().create_work_order("TKT-000001", "FRZ-1043", "TECH-02", start, 4)
    assert result.data.id == "WO-000001"
    assert json.loads(api.calls[0].content)["scheduled_start"] == "2026-10-01T10:00:00+05:30"


def test_create_purchase_order_payload() -> None:
    po = {
        "id": "PO-000001",
        "vendor_id": "VEN-03",
        "ticket_id": "TKT-000001",
        "status": "draft",
        "total_lkr": 115000,
        "warranty_claim": True,
        "created_by": "agent",
        "created_at": "2026-10-01T09:00:00+05:30",
        "lines": [{"sku": "CMP-FL", "qty": 1, "unit_price_lkr": 115000}],
    }
    api = FakeAPI({("POST", "/erp/purchase-orders"): respond(201, po)})
    result = api.tools().create_purchase_order("VEN-03", [PartNeed(sku="CMP-FL")], "TKT-000001", True)
    assert result.data.total_lkr == 115000
    assert json.loads(api.calls[0].content) == {
        "vendor_id": "VEN-03",
        "ticket_id": "TKT-000001",
        "lines": [{"sku": "CMP-FL", "qty": 1}],
        "warranty_claim": True,
        "created_by": "agent",
    }


def test_update_ticket_sends_only_given_fields() -> None:
    api = FakeAPI({("PATCH", "/crm/tickets/TKT-000001"): respond(200, TICKET)})
    deadline = NOW + timedelta(hours=4)
    result = api.tools().update_ticket("TKT-000001", {"priority": "P1", "sla_deadline": deadline})
    assert result.ok
    assert json.loads(api.calls[0].content) == {"priority": "P1", "sla_deadline": "2026-10-01T13:00:00+05:30"}


@pytest.mark.parametrize("fields", [{"priorty": "P1"}, {"status": "done"}, {"priority": "P9"}])
def test_update_ticket_rejects_bad_fields_without_calling_api(fields: dict) -> None:
    api = FakeAPI({})
    result = api.tools().update_ticket("TKT-000001", fields)
    assert result.error.code == "invalid_input"
    assert api.calls == []
