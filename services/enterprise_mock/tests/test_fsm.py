"""FSM endpoints, including the FreshMart demo case."""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.conftest import SEED_TODAY, assert_error

WINDOW = {
    "from": f"{SEED_TODAY.isoformat()}T00:00:00+05:30",
    "to": f"{SEED_TODAY.isoformat()}T23:59:00+05:30",
}


def _window(days: int) -> dict[str, str]:
    end = SEED_TODAY + timedelta(days=days)
    return {"from": WINDOW["from"], "to": f"{end.isoformat()}T23:59:00+05:30"}


# --- demo case ------------------------------------------------------------------


def test_demo_asset_is_found_by_name(client: TestClient) -> None:
    assets = client.get("/fsm/assets", params={"customer_id": "CUST-001", "q": "Freezer #3"}).json()
    assert [a["id"] for a in assets] == ["FRZ-1043"]
    asset = assets[0]
    assert asset["site"] == "Colombo 7"
    assert asset["region"] == "Colombo"
    assert asset["warranty_end"] > SEED_TODAY.isoformat()  # under warranty


def test_demo_asset_history(client: TestClient) -> None:
    history = client.get("/fsm/assets/FRZ-1043/history").json()
    latest = history[0]  # newest first
    assert latest["fault_code"] == "COMP_FAIL"
    assert latest["technician_id"] == "TECH-02"
    assert latest["date"] == (SEED_TODAY - timedelta(days=60)).isoformat()
    assert latest["parts_used"] == [{"sku": "CMP-AP", "qty": 1}]
    assert [h["date"] for h in history] == sorted((h["date"] for h in history), reverse=True)


# --- assets ---------------------------------------------------------------------


def test_asset_search_matches_every_word(client: TestClient) -> None:
    assets = client.get("/fsm/assets", params={"customer_id": "CUST-001", "q": "freezer colombo 7"}).json()
    assert [a["id"] for a in assets] == ["FRZ-1041", "FRZ-1042", "FRZ-1043"]
    all_freshmart = client.get("/fsm/assets", params={"customer_id": "CUST-001"}).json()
    assert [a["id"] for a in all_freshmart] == ["CHL-1044", "FRZ-1041", "FRZ-1042", "FRZ-1043"]


def test_asset_not_found(client: TestClient) -> None:
    assert_error(client.get("/fsm/assets/FRZ-0000"), 404, "not_found")
    assert_error(client.get("/fsm/assets/FRZ-0000/history"), 404, "not_found")


def test_fault_codes_filtered_by_model(client: TestClient) -> None:
    assert len(client.get("/fsm/fault-codes").json()) == 12
    freezer_codes = {f["code"] for f in client.get("/fsm/fault-codes", params={"model": "FL-140"}).json()}
    chiller_codes = {f["code"] for f in client.get("/fsm/fault-codes", params={"model": "CC-75"}).json()}
    assert "DEFROST_HEATER" in freezer_codes
    assert "DEFROST_HEATER" not in chiller_codes
    assert "COMP_FAIL" in freezer_codes & chiller_codes


# --- technicians ----------------------------------------------------------------


def test_available_technicians_filtered_by_region_and_skill(client: TestClient) -> None:
    params = {"region": "Colombo", "skill": "refrigeration", **_window(2)}
    techs = client.get("/fsm/technicians/available", params=params).json()
    assert {t["technician_id"] for t in techs} == {"TECH-01", "TECH-02"}
    window_start = datetime.fromisoformat(params["from"])
    window_end = datetime.fromisoformat(params["to"])
    for tech in techs:
        assert "refrigeration" in tech["skills"]
        for slot in tech["slots"]:
            assert window_start <= datetime.fromisoformat(slot["start"]) < datetime.fromisoformat(slot["end"])
            assert datetime.fromisoformat(slot["end"]) <= window_end
            assert slot["jobs_that_day"] >= 0


def test_busy_technician_has_no_slots_in_first_days(client: TestClient) -> None:
    params = {"region": "Kurunegala", **_window(4)}
    assert client.get("/fsm/technicians/available", params=params).json() == []
    later = client.get("/fsm/technicians/available", params={"region": "Kurunegala", **_window(13)}).json()
    assert [t["technician_id"] for t in later] == ["TECH-08"]


def test_available_technicians_rejects_inverted_window(client: TestClient) -> None:
    params = {"from": WINDOW["to"], "to": WINDOW["from"]}
    assert_error(client.get("/fsm/technicians/available", params=params), 422, "invalid_window")


# --- work orders ----------------------------------------------------------------


def _two_consecutive_open_slots(client: TestClient, tech_id: str) -> str:
    """Start time of the first open slot that is followed by another open slot."""
    techs = client.get("/fsm/technicians/available", params={"region": "Colombo", **_window(13)}).json()
    slots = next(t["slots"] for t in techs if t["technician_id"] == tech_id)
    return next(a["start"] for a, b in zip(slots, slots[1:], strict=False) if a["end"] == b["start"])


def test_create_work_order_books_slots(client: TestClient, ticket: dict) -> None:
    start = _two_consecutive_open_slots(client, "TECH-02")
    payload = {
        "ticket_id": ticket["id"],
        "asset_id": "FRZ-1043",
        "technician_id": "TECH-02",
        "scheduled_start": start,
        "est_hours": 4,
    }
    response = client.post("/fsm/work-orders", json=payload)
    assert response.status_code == 201, response.text
    work_order = response.json()
    assert work_order["id"] == "WO-000001"
    assert work_order["status"] == "scheduled"
    assert datetime.fromisoformat(work_order["scheduled_start"]) == datetime.fromisoformat(start)
    assert client.get(f"/fsm/work-orders/{work_order['id']}").json() == work_order

    # Both slots are now gone from the calendar, and double booking is refused.
    techs = client.get("/fsm/technicians/available", params={"region": "Colombo", **_window(13)}).json()
    tech02 = next(t for t in techs if t["technician_id"] == "TECH-02")
    booked_end = datetime.fromisoformat(start) + timedelta(hours=4)
    assert all(
        not (datetime.fromisoformat(start) <= datetime.fromisoformat(s["start"]) < booked_end)
        for s in tech02["slots"]
    )
    assert_error(client.post("/fsm/work-orders", json=payload), 409, "slot_unavailable")


def test_work_order_must_start_on_a_slot(client: TestClient, ticket: dict) -> None:
    payload = {
        "ticket_id": ticket["id"],
        "asset_id": "FRZ-1043",
        "technician_id": "TECH-02",
        "scheduled_start": f"{(SEED_TODAY + timedelta(days=1)).isoformat()}T09:00:00+05:30",
        "est_hours": 1,
    }
    assert_error(client.post("/fsm/work-orders", json=payload), 409, "slot_unavailable")


def test_work_order_validates_references(client: TestClient, ticket: dict) -> None:
    payload = {
        "ticket_id": ticket["id"],
        "asset_id": "FRZ-1043",
        "technician_id": "TECH-99",
        "scheduled_start": WINDOW["from"],
        "est_hours": 1,
    }
    assert_error(client.post("/fsm/work-orders", json=payload), 422, "invalid_reference")
    assert_error(client.get("/fsm/work-orders/WO-999999"), 404, "not_found")
