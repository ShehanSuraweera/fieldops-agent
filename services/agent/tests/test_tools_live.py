"""Read-only checks of the tools against the running enterprise mock.

Skipped when the mock is not reachable. Nothing here writes data, so it is safe
to run against the dev database.
"""

from datetime import timedelta

import httpx
import pytest

from app.config import now_lk, settings
from app.tools import EnterpriseTools


def _mock_is_up() -> bool:
    try:
        return httpx.get(f"{settings.mock_base_url}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _mock_is_up(), reason=f"enterprise mock not reachable at {settings.mock_base_url}"
)


@pytest.fixture(scope="module")
def tools() -> EnterpriseTools:
    return EnterpriseTools()


def test_find_freshmart(tools: EnterpriseTools) -> None:
    result = tools.find_customer("ops@freshmart.lk")
    assert result.ok and result.data.id == "CUST-001"
    assert result.data.contract_tier == "gold"


def test_find_freezer_3(tools: EnterpriseTools) -> None:
    assets = tools.find_asset("CUST-001", "Freezer #3").data
    assert [a.id for a in assets] == ["FRZ-1043"]


def test_demo_asset_summary(tools: EnterpriseTools) -> None:
    facts = tools.get_asset_summary("FRZ-1043").data.facts
    assert facts.under_warranty
    assert facts.repeat_failure_90d
    assert facts.last_fault_code == "COMP_FAIL"
    assert facts.last_technician_id == "TECH-02"


def test_catalog_stock_and_vendors(tools: EnterpriseTools) -> None:
    codes = {f.code for f in tools.list_fault_codes("AP-500").data}
    assert "COMP_FAIL" in codes
    assert tools.check_part_stock("CMP-AP").data.compatible_models == ["AP-500", "AP-900"]
    offers = tools.find_vendors("CMP-FL").data
    assert any(o.approved for o in offers) and any(not o.approved for o in offers)


def test_technician_availability(tools: EnterpriseTools) -> None:
    start = now_lk()
    techs = tools.find_available_technicians(
        "Colombo", "refrigeration", start, start + timedelta(days=7)
    ).data
    assert {t.technician_id for t in techs} <= {"TECH-01", "TECH-02"}


def test_unknown_customer_is_a_failed_result(tools: EnterpriseTools) -> None:
    result = tools.find_customer("nobody@example.com")
    assert not result.ok and result.error.code == "not_found"
