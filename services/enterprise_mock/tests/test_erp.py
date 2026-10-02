"""ERP endpoints: parts, reservations, vendors and purchase orders."""

from fastapi.testclient import TestClient

from tests.conftest import assert_error


def test_parts_filtered_by_model_and_query(client: TestClient) -> None:
    assert len(client.get("/erp/parts").json()) == 30
    ap500 = {p["sku"] for p in client.get("/erp/parts", params={"model": "AP-500"}).json()}
    assert "CMP-AP" in ap500
    assert "CMP-FL" not in ap500
    compressors = client.get("/erp/parts", params={"q": "compressor"}).json()
    assert {"CMP-AP", "CMP-FL", "CMP-PM", "CMP-CC"} <= {p["sku"] for p in compressors}


def test_get_part(client: TestClient) -> None:
    part = client.get("/erp/parts/CMP-AP").json()
    assert part["stock_qty"] == 2
    assert part["unit_cost_lkr"] == 92_000
    assert_error(client.get("/erp/parts/NOPE"), 404, "not_found")


def test_reservation_reduces_stock(client: TestClient, ticket: dict) -> None:
    response = client.post("/erp/reservations", json={"sku": "CMP-AP", "qty": 1, "ticket_id": ticket["id"]})
    assert response.status_code == 201
    assert response.json()["stock_qty_after"] == 1
    assert client.get("/erp/parts/CMP-AP").json()["stock_qty"] == 1


def test_reservation_refuses_insufficient_stock(client: TestClient, ticket: dict) -> None:
    response = client.post("/erp/reservations", json={"sku": "CMP-AP", "qty": 3, "ticket_id": ticket["id"]})
    error = assert_error(response, 409, "insufficient_stock")
    assert error["details"] == {"sku": "CMP-AP", "available": 2, "requested": 3}
    assert client.get("/erp/parts/CMP-AP").json()["stock_qty"] == 2


def test_reservation_validation(client: TestClient, ticket: dict) -> None:
    bad_qty = {"sku": "CMP-AP", "qty": 0, "ticket_id": ticket["id"]}
    assert_error(client.post("/erp/reservations", json=bad_qty), 422, "validation_error")
    bad_ticket = {"sku": "CMP-AP", "qty": 1, "ticket_id": "TKT-999999"}
    assert_error(client.post("/erp/reservations", json=bad_ticket), 422, "invalid_reference")


def test_vendors(client: TestClient) -> None:
    all_vendors = client.get("/erp/vendors").json()
    assert len(all_vendors) == 6
    assert all(v["price_lkr"] is None for v in all_vendors)

    for_sku = client.get("/erp/vendors", params={"sku": "CMP-FL"}).json()
    assert {"VEN-03", "VEN-05"} <= {v["id"] for v in for_sku}
    assert any(v["approved"] for v in for_sku) and any(not v["approved"] for v in for_sku)
    assert all(v["price_lkr"] > 0 for v in for_sku)
    assert_error(client.get("/erp/vendors", params={"sku": "NOPE"}), 404, "not_found")


def _price(client: TestClient, vendor_id: str, sku: str) -> int:
    vendors = client.get("/erp/vendors", params={"sku": sku}).json()
    return next(v["price_lkr"] for v in vendors if v["id"] == vendor_id)


def _create_po(client: TestClient, ticket_id: str, **overrides) -> dict:
    payload = {
        "vendor_id": "VEN-03",
        "ticket_id": ticket_id,
        "lines": [{"sku": "CMP-FL", "qty": 1}, {"sku": "DRY-FILTER", "qty": 2}],
        "warranty_claim": True,
        **overrides,
    }
    return client.post("/erp/purchase-orders", json=payload)


def test_create_purchase_order_prices_from_vendor_list(client: TestClient, ticket: dict) -> None:
    response = _create_po(client, ticket["id"])
    assert response.status_code == 201, response.text
    po = response.json()
    expected = _price(client, "VEN-03", "CMP-FL") + 2 * _price(client, "VEN-03", "DRY-FILTER")
    assert po["id"] == "PO-000001"
    assert po["status"] == "draft"
    assert po["total_lkr"] == expected
    assert po["warranty_claim"] is True
    assert po["created_by"] == "agent"
    assert {line["sku"]: line["qty"] for line in po["lines"]} == {"CMP-FL": 1, "DRY-FILTER": 2}
    assert client.get(f"/erp/purchase-orders/{po['id']}").json() == po


def test_purchase_order_rules(client: TestClient, ticket: dict) -> None:
    assert_error(_create_po(client, ticket["id"], vendor_id="VEN-05"), 422, "vendor_not_approved")
    assert_error(_create_po(client, ticket["id"], vendor_id="VEN-99"), 422, "invalid_reference")
    duplicate = [{"sku": "CMP-FL", "qty": 1}, {"sku": "CMP-FL", "qty": 1}]
    assert_error(_create_po(client, ticket["id"], lines=duplicate), 422, "duplicate_line")
    assert_error(_create_po(client, ticket["id"], lines=[]), 422, "validation_error")

    # Every part has one or two of VEN-01/02/04, so at least one does not supply CMP-FL.
    suppliers = {v["id"] for v in client.get("/erp/vendors", params={"sku": "CMP-FL"}).json()}
    non_supplier = next(v for v in ("VEN-01", "VEN-02", "VEN-04") if v not in suppliers)
    response = _create_po(client, ticket["id"], vendor_id=non_supplier, lines=[{"sku": "CMP-FL", "qty": 1}])
    error = assert_error(response, 422, "sku_not_supplied")
    assert error["details"]["skus"] == ["CMP-FL"]


def test_purchase_order_approval_flow(client: TestClient, ticket: dict) -> None:
    po_id = _create_po(client, ticket["id"]).json()["id"]
    for status in ("pending_approval", "approved", "sent"):
        response = client.patch(f"/erp/purchase-orders/{po_id}", json={"status": status})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == status
    error = assert_error(
        client.patch(f"/erp/purchase-orders/{po_id}", json={"status": "draft"}), 409, "invalid_transition"
    )
    assert error["details"]["from"] == "sent"


def test_purchase_order_rejection(client: TestClient, ticket: dict) -> None:
    po_id = _create_po(client, ticket["id"]).json()["id"]
    assert_error(
        client.patch(f"/erp/purchase-orders/{po_id}", json={"status": "rejected"}), 409, "invalid_transition"
    )
    client.patch(f"/erp/purchase-orders/{po_id}", json={"status": "pending_approval"})
    assert (
        client.patch(f"/erp/purchase-orders/{po_id}", json={"status": "rejected"}).json()["status"]
        == "rejected"
    )
    assert_error(
        client.patch(f"/erp/purchase-orders/{po_id}", json={"status": "sent"}), 409, "invalid_transition"
    )
    assert_error(client.patch("/erp/purchase-orders/PO-999999", json={"status": "sent"}), 404, "not_found")
