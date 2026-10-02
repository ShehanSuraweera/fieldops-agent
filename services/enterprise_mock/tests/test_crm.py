"""CRM endpoints: customers, tickets and messages."""

from fastapi.testclient import TestClient

from tests.conftest import assert_error


def test_find_customer_by_email_is_case_insensitive(client: TestClient) -> None:
    customers = client.get("/crm/customers", params={"email": "OPS@FreshMart.lk"}).json()
    assert [c["id"] for c in customers] == ["CUST-001"]
    assert customers[0] == {
        "id": "CUST-001",
        "name": "FreshMart",
        "region": "Colombo",
        "contract_tier": "gold",
        "contact_email": "ops@freshmart.lk",
    }


def test_unknown_email_returns_empty_list(client: TestClient) -> None:
    assert client.get("/crm/customers", params={"email": "nobody@example.com"}).json() == []


def test_list_and_get_customer(client: TestClient) -> None:
    assert len(client.get("/crm/customers").json()) == 20
    assert client.get("/crm/customers/CUST-011").json()["name"] == "Fort Fresh"


def test_create_ticket(client: TestClient, ticket: dict) -> None:
    assert ticket["id"] == "TKT-000001"
    assert ticket["status"] == "new"
    assert ticket["asset_id"] == "FRZ-1043"
    assert ticket["priority"] is None
    assert ticket["messages"] == []
    second = client.post("/crm/tickets", json={"customer_id": "CUST-002", "description": "Chiller noisy"})
    assert second.json()["id"] == "TKT-000002"


def test_create_ticket_rejects_unknown_customer(client: TestClient) -> None:
    response = client.post("/crm/tickets", json={"customer_id": "CUST-999", "description": "x"})
    assert_error(response, 422, "invalid_reference")


def test_create_ticket_rejects_other_customers_asset(client: TestClient) -> None:
    response = client.post(
        "/crm/tickets", json={"customer_id": "CUST-002", "asset_id": "FRZ-1043", "description": "x"}
    )
    assert_error(response, 422, "asset_customer_mismatch")


def test_create_ticket_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post(
        "/crm/tickets", json={"customer_id": "CUST-001", "description": "x", "priorty": "P1"}
    )
    assert_error(response, 422, "validation_error")


def test_get_ticket(client: TestClient, ticket: dict) -> None:
    assert client.get(f"/crm/tickets/{ticket['id']}").json() == ticket
    assert_error(client.get("/crm/tickets/TKT-999999"), 404, "not_found")


def test_patch_ticket(client: TestClient, ticket: dict) -> None:
    response = client.patch(
        f"/crm/tickets/{ticket['id']}",
        json={
            "status": "scheduled",
            "priority": "P1",
            "sla_deadline": "2026-10-01T14:00:00+05:30",
            "resolution_note": "Warranty claim. Supervisor review: repeat failure.",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["priority"] == "P1"
    assert body["sla_deadline"].startswith("2026-10-01T08:30:00")  # stored as UTC
    assert body["resolution_note"].startswith("Warranty claim")
    assert body["description"] == ticket["description"]


def test_patch_ticket_only_changes_sent_fields(client: TestClient, ticket: dict) -> None:
    client.patch(f"/crm/tickets/{ticket['id']}", json={"priority": "P2"})
    body = client.patch(f"/crm/tickets/{ticket['id']}", json={"status": "open"}).json()
    assert body["priority"] == "P2"
    assert body["status"] == "open"


def test_patch_ticket_validation(client: TestClient, ticket: dict) -> None:
    url = f"/crm/tickets/{ticket['id']}"
    assert_error(client.patch(url, json={"status": "bogus"}), 422, "validation_error")
    assert_error(client.patch(url, json={"status": None}), 422, "validation_error")
    assert_error(client.patch(url, json={"priority": "P9"}), 422, "validation_error")
    other_asset = client.get("/fsm/assets", params={"customer_id": "CUST-002"}).json()[0]["id"]
    assert_error(client.patch(url, json={"asset_id": other_asset}), 422, "asset_customer_mismatch")
    assert_error(client.patch("/crm/tickets/TKT-999999", json={"status": "open"}), 404, "not_found")


def test_log_outbound_message(client: TestClient, ticket: dict) -> None:
    response = client.post(
        f"/crm/tickets/{ticket['id']}/messages", json={"body": "A technician will visit at 10:00."}
    )
    assert response.status_code == 201
    message = response.json()
    assert message["direction"] == "outbound"
    assert message["ticket_id"] == ticket["id"]
    messages = client.get(f"/crm/tickets/{ticket['id']}").json()["messages"]
    assert [m["body"] for m in messages] == ["A technician will visit at 10:00."]


def test_message_to_unknown_ticket_is_404(client: TestClient) -> None:
    assert_error(client.post("/crm/tickets/TKT-999999/messages", json={"body": "hi"}), 404, "not_found")
