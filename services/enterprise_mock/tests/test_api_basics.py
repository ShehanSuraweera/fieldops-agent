"""API key, error body shape and the OpenAPI surface."""

from fastapi.testclient import TestClient

from tests.conftest import assert_error

EXPECTED_OPERATIONS = {
    ("get", "/crm/customers"),
    ("get", "/crm/customers/{customer_id}"),
    ("post", "/crm/tickets"),
    ("get", "/crm/tickets/{ticket_id}"),
    ("patch", "/crm/tickets/{ticket_id}"),
    ("post", "/crm/tickets/{ticket_id}/messages"),
    ("get", "/fsm/assets"),
    ("get", "/fsm/assets/{asset_id}"),
    ("get", "/fsm/assets/{asset_id}/history"),
    ("get", "/fsm/fault-codes"),
    ("get", "/fsm/technicians/available"),
    ("post", "/fsm/work-orders"),
    ("get", "/fsm/work-orders/{work_order_id}"),
    ("get", "/erp/parts"),
    ("get", "/erp/parts/{sku}"),
    ("post", "/erp/reservations"),
    ("get", "/erp/vendors"),
    ("post", "/erp/purchase-orders"),
    ("get", "/erp/purchase-orders/{po_id}"),
    ("patch", "/erp/purchase-orders/{po_id}"),
}


def test_health_needs_no_key(anon_client: TestClient) -> None:
    assert anon_client.get("/health").json() == {"status": "ok"}


def test_missing_api_key_is_401(anon_client: TestClient) -> None:
    assert_error(anon_client.get("/crm/customers"), 401, "unauthorized")


def test_wrong_api_key_is_401(anon_client: TestClient) -> None:
    assert_error(anon_client.get("/erp/parts", headers={"X-API-Key": "wrong"}), 401, "unauthorized")


def test_not_found_error_shape(client: TestClient) -> None:
    error = assert_error(client.get("/crm/customers/CUST-999"), 404, "not_found")
    assert error["details"] == {"resource": "customer", "id": "CUST-999"}


def test_unknown_route_uses_error_shape(client: TestClient) -> None:
    assert_error(client.get("/nope"), 404, "not_found")


def test_validation_error_shape(client: TestClient) -> None:
    error = assert_error(
        client.post("/crm/tickets", json={"customer_id": "CUST-001"}), 422, "validation_error"
    )
    assert any(item["loc"][-1] == "description" for item in error["details"])


def test_docs_list_every_endpoint(anon_client: TestClient) -> None:
    assert anon_client.get("/docs").status_code == 200
    spec = anon_client.get("/openapi.json").json()
    operations = {(method, path) for path, item in spec["paths"].items() for method in item}
    assert operations >= EXPECTED_OPERATIONS
    # Protected routes advertise the API key and the shared error body.
    customers = spec["paths"]["/crm/customers"]["get"]
    assert customers["security"] == [{"APIKeyHeader": []}]
    assert "ErrorResponse" in str(customers["responses"]["404"])
