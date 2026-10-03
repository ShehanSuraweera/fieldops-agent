"""Unit tests for the business rules. No network, database or LLM."""

from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.config import LK_TZ
from app.enterprise import Asset, PartNeed, ServiceRecord, Slot, TechnicianAvailability, VendorOffer
from app.rules import (
    RulesConfig,
    choose_technician,
    choose_vendor,
    compute_asset_facts,
    load_rules,
    needs_inspection,
    parts_ready_at,
    plan_parts,
    po_requires_approval,
    priority,
    sla_deadline,
    ticket_notes,
)

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=LK_TZ)  # Thursday 09:00 Colombo
TODAY = NOW.date()


@pytest.fixture
def cfg() -> RulesConfig:
    return RulesConfig(
        sla_hours={"gold": 4, "silver": 24, "bronze": 72},
        po_approval_threshold_lkr=100_000,
        repeat_failure_window_days=90,
        diagnosis_min_confidence=0.6,
    )


# --- configuration -------------------------------------------------------------


def test_rules_yaml_matches_spec(cfg: RulesConfig) -> None:
    assert load_rules() == cfg  # the real config/rules.yaml


def test_config_rejects_missing_tier() -> None:
    with pytest.raises(ValidationError, match="missing tiers"):
        RulesConfig(
            sla_hours={"gold": 4, "silver": 24},
            po_approval_threshold_lkr=1,
            repeat_failure_window_days=90,
            diagnosis_min_confidence=0.6,
        )


@pytest.mark.parametrize(
    "override",
    [
        {"diagnosis_min_confidence": 1.5},
        {"po_approval_threshold_lkr": -1},
        {"repeat_failure_window_days": 0},
        {"sla_hours": {"gold": 0, "silver": 24, "bronze": 72}},
        {"unknown_threshold": 3},
    ],
)
def test_config_rejects_bad_values(cfg: RulesConfig, override: dict) -> None:
    with pytest.raises(ValidationError):
        RulesConfig.model_validate({**cfg.model_dump(), **override})


def test_thresholds_come_from_config(cfg: RulesConfig) -> None:
    strict = cfg.model_copy(
        update={"po_approval_threshold_lkr": 50_000, "sla_hours": {**cfg.sla_hours, "gold": 2}}
    )
    assert po_requires_approval(60_000, strict)
    assert not po_requires_approval(60_000, cfg)
    assert sla_deadline(NOW, "gold", strict) == NOW + timedelta(hours=2)


# --- SLA -----------------------------------------------------------------------


@pytest.mark.parametrize(("tier", "hours"), [("gold", 4), ("silver", 24), ("bronze", 72)])
def test_sla_deadline_by_tier(cfg: RulesConfig, tier: str, hours: int) -> None:
    assert sla_deadline(NOW, tier, cfg) == NOW + timedelta(hours=hours)


def test_sla_deadline_requires_timezone(cfg: RulesConfig) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        sla_deadline(datetime(2026, 10, 1, 9), "gold", cfg)


# --- priority ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tier", "asset_down", "repeat", "expected"),
    [
        ("gold", True, False, "P1"),
        ("gold", False, False, "P2"),
        ("silver", True, False, "P2"),
        ("silver", False, False, "P2"),
        ("bronze", True, False, "P3"),
        ("bronze", True, True, "P2"),  # repeat failure raises one level
        ("silver", False, True, "P1"),
        ("gold", False, True, "P1"),
        ("gold", True, True, "P1"),  # P1 is the ceiling
    ],
)
def test_priority(tier: str, asset_down: bool, repeat: bool, expected: str) -> None:
    assert priority(tier, asset_down, repeat) == expected


# --- asset facts ---------------------------------------------------------------


def _asset(warranty_end: date = date(2027, 8, 8), install: date = date(2025, 8, 8)) -> Asset:
    return Asset(
        id="FRZ-1043",
        customer_id="CUST-001",
        name="Freezer #3",
        model="AP-500",
        serial="SN-1",
        site="Colombo 7",
        region="Colombo",
        install_date=install,
        warranty_end=warranty_end,
    )


def _record(record_id: int, days_ago: int, fault: str, tech: str) -> ServiceRecord:
    return ServiceRecord(
        id=record_id,
        asset_id="FRZ-1043",
        date=TODAY - timedelta(days=days_ago),
        fault_code=fault,
        notes="",
        parts_used=[],
        technician_id=tech,
    )


def test_asset_facts_for_demo_case(cfg: RulesConfig) -> None:
    history = [_record(1, 300, "THERMO_FAIL", "TECH-01"), _record(2, 60, "COMP_FAIL", "TECH-02")]
    facts = compute_asset_facts(_asset(), history, TODAY, cfg)
    assert facts.under_warranty
    assert facts.repeat_failure_90d
    assert facts.last_technician_id == "TECH-02"
    assert facts.last_fault_code == "COMP_FAIL"
    assert facts.previous_technician_ids == ["TECH-02", "TECH-01"]
    assert [f.fault_code for f in facts.recent_faults] == ["COMP_FAIL"]
    assert facts.age_years == 1.1


def test_repeat_failure_window_boundary(cfg: RulesConfig) -> None:
    on_edge = compute_asset_facts(_asset(), [_record(1, 90, "REF_LEAK", "TECH-01")], TODAY, cfg)
    outside = compute_asset_facts(_asset(), [_record(1, 91, "REF_LEAK", "TECH-01")], TODAY, cfg)
    assert on_edge.repeat_failure_90d
    assert not outside.repeat_failure_90d
    assert outside.recent_faults == []


def test_warranty_boundary(cfg: RulesConfig) -> None:
    assert compute_asset_facts(_asset(warranty_end=TODAY), [], TODAY, cfg).under_warranty
    expired = compute_asset_facts(_asset(warranty_end=TODAY - timedelta(days=1)), [], TODAY, cfg)
    assert not expired.under_warranty


def test_asset_facts_without_history(cfg: RulesConfig) -> None:
    facts = compute_asset_facts(_asset(), [], TODAY, cfg)
    assert not facts.repeat_failure_90d
    assert facts.last_technician_id is None
    assert facts.previous_technician_ids == []


def test_asset_facts_ignore_future_records(cfg: RulesConfig) -> None:
    facts = compute_asset_facts(_asset(), [_record(1, -5, "COMP_FAIL", "TECH-02")], TODAY, cfg)
    assert facts.last_service_date is None


def test_ticket_notes(cfg: RulesConfig) -> None:
    history = [_record(2, 60, "COMP_FAIL", "TECH-02")]
    notes = ticket_notes(compute_asset_facts(_asset(), history, TODAY, cfg))
    assert len(notes) == 2
    assert notes[0].startswith("Warranty claim")
    assert notes[1].startswith("Supervisor review") and "TECH-02" in notes[1]
    expired = _asset(warranty_end=TODAY - timedelta(days=1))
    assert ticket_notes(compute_asset_facts(expired, [], TODAY, cfg)) == []


# --- diagnosis -----------------------------------------------------------------


@pytest.mark.parametrize(("confidence", "inspect"), [(0.59, True), (0.6, False), (0.95, False), (0.0, True)])
def test_needs_inspection(cfg: RulesConfig, confidence: float, inspect: bool) -> None:
    assert needs_inspection(confidence, cfg) is inspect


# --- parts ---------------------------------------------------------------------


def test_plan_parts_splits_stock_and_po() -> None:
    plan = plan_parts(
        [PartNeed(sku="CMP-AP", qty=1), PartNeed(sku="DRY-FILTER", qty=2)], {"CMP-AP": 2, "DRY-FILTER": 1}
    )
    assert [(p.sku, p.status) for p in plan] == [("CMP-AP", "in_stock"), ("DRY-FILTER", "needs_po")]


def test_plan_parts_merges_duplicates_and_handles_unknown_stock() -> None:
    plan = plan_parts([PartNeed(sku="SNS-NTC"), PartNeed(sku="SNS-NTC"), PartNeed(sku="NEW")], {"SNS-NTC": 2})
    assert [(p.sku, p.qty, p.status) for p in plan] == [("SNS-NTC", 2, "in_stock"), ("NEW", 1, "needs_po")]


# --- procurement ---------------------------------------------------------------


def _offer(vendor: str, price: int, lead: int, approved: bool = True, sku: str = "CMP-FL") -> VendorOffer:
    return VendorOffer(
        vendor_id=vendor, vendor_name=vendor, sku=sku, approved=approved, lead_time_days=lead, price_lkr=price
    )


def test_vendor_cheapest_that_meets_sla() -> None:
    offers = [_offer("VEN-01", 129_000, 2), _offer("VEN-02", 140_000, 1), _offer("VEN-03", 115_000, 5)]
    choice = choose_vendor([PartNeed(sku="CMP-FL")], offers, NOW, NOW + timedelta(hours=72))
    assert choice.vendor_id == "VEN-01"
    assert not choice.sla_risk
    assert choice.total_lkr == 129_000
    assert choice.parts_ready_at == NOW + timedelta(days=2)


def test_vendor_never_unapproved_even_if_cheapest_and_fastest() -> None:
    offers = [_offer("VEN-05", 90_000, 1, approved=False), _offer("VEN-03", 115_000, 2)]
    choice = choose_vendor([PartNeed(sku="CMP-FL")], offers, NOW, NOW + timedelta(hours=72))
    assert choice.vendor_id == "VEN-03"


def test_vendor_fastest_with_sla_risk_when_none_meet_sla() -> None:
    offers = [_offer("VEN-01", 129_000, 2), _offer("VEN-02", 140_000, 1), _offer("VEN-03", 115_000, 5)]
    choice = choose_vendor([PartNeed(sku="CMP-FL")], offers, NOW, NOW + timedelta(hours=4))
    assert choice.vendor_id == "VEN-02"
    assert choice.sla_risk
    assert "SLA at risk" in choice.reason


def test_vendor_tie_breaks_on_lead_time_then_id() -> None:
    offers = [_offer("VEN-04", 100_000, 3), _offer("VEN-01", 100_000, 2), _offer("VEN-02", 100_000, 2)]
    choice = choose_vendor([PartNeed(sku="CMP-FL")], offers, NOW, NOW + timedelta(days=10))
    assert choice.vendor_id == "VEN-01"


def test_vendor_must_supply_every_line_and_totals_quantities() -> None:
    offers = [
        _offer("VEN-01", 2_000, 2, sku="DRY-FILTER"),
        _offer("VEN-03", 3_000, 1, sku="DRY-FILTER"),
        _offer("VEN-03", 24_000, 1, sku="GAS-R290"),
    ]
    lines = [PartNeed(sku="DRY-FILTER", qty=2), PartNeed(sku="GAS-R290", qty=1)]
    choice = choose_vendor(lines, offers, NOW, NOW + timedelta(days=3))
    assert choice.vendor_id == "VEN-03"
    assert choice.total_lkr == 2 * 3_000 + 24_000


def test_vendor_none_when_no_approved_supplier() -> None:
    offers = [_offer("VEN-05", 90_000, 1, approved=False)]
    assert choose_vendor([PartNeed(sku="CMP-FL")], offers, NOW, NOW + timedelta(days=3)) is None
    assert choose_vendor([], offers, NOW, NOW + timedelta(days=3)) is None


@pytest.mark.parametrize(
    ("total", "required"), [(100_001, True), (100_000, False), (99_999, False), (0, False)]
)
def test_po_approval_threshold(cfg: RulesConfig, total: int, required: bool) -> None:
    assert po_requires_approval(total, cfg) is required


def test_parts_ready_at() -> None:
    assert parts_ready_at(NOW, 3) == NOW + timedelta(days=3)


# --- scheduling ----------------------------------------------------------------


def _at(day_offset: int, hour: int) -> datetime:
    return datetime.combine(TODAY + timedelta(days=day_offset), datetime.min.time(), tzinfo=LK_TZ).replace(
        hour=hour
    )


_slot_ids = iter(range(1, 10_000))


def _tech(tech_id: str, slots: list[tuple[int, int, int]], *, region="Colombo", skills=("refrigeration",)):
    """slots: (day_offset, start_hour, jobs_that_day); each slot is 2 hours."""
    return TechnicianAvailability(
        technician_id=tech_id,
        name=tech_id.title(),
        region=region,
        skills=list(skills),
        slots=[
            Slot(slot_id=next(_slot_ids), start=_at(d, h), end=_at(d, h + 2), jobs_that_day=j)
            for d, h, j in slots
        ],
    )


def _choose(candidates, *, prior=(), earliest=NOW, sla_hours=4, est_hours=2.0, skill="refrigeration"):
    return choose_technician(
        candidates,
        region="Colombo",
        skill=skill,
        prior_technician_ids=list(prior),
        earliest=earliest,
        sla_deadline=NOW + timedelta(hours=sla_hours),
        est_hours=est_hours,
    )


def test_technician_prior_work_beats_earlier_slot() -> None:
    techs = [_tech("TECH-01", [(0, 10, 0)]), _tech("TECH-02", [(0, 12, 3)])]
    choice = _choose(techs, prior=["TECH-02"])
    assert choice.technician_id == "TECH-02"
    assert choice.prior_work_on_asset
    assert not choice.sla_risk


def test_technician_earliest_slot_then_fewest_jobs() -> None:
    techs = [_tech("TECH-01", [(0, 12, 0)]), _tech("TECH-02", [(0, 10, 4)]), _tech("TECH-03", [(0, 10, 1)])]
    choice = _choose(techs)
    assert choice.technician_id == "TECH-03"
    assert choice.start == _at(0, 10)


def test_technician_must_have_skill_and_region() -> None:
    techs = [
        _tech("TECH-03", [(0, 10, 0)], skills=("electrical",)),
        _tech("TECH-04", [(0, 10, 0)], region="Kandy"),
    ]
    assert _choose(techs) is None


def test_technician_ignores_slots_before_earliest() -> None:
    techs = [_tech("TECH-01", [(0, 8, 0), (0, 12, 0)])]
    assert _choose(techs).start == _at(0, 12)


def test_technician_needs_consecutive_slots_for_long_jobs() -> None:
    gap = _tech("TECH-01", [(0, 10, 0), (0, 14, 0)])  # 10-12 and 14-16: no 4-hour block
    block = _tech("TECH-02", [(1, 10, 0), (1, 12, 0)])
    choice = _choose([gap, block], est_hours=4, sla_hours=72)
    assert choice.technician_id == "TECH-02"
    assert choice.end - choice.start == timedelta(hours=4)
    assert len(choice.slot_ids) == 2


def test_technician_outside_sla_flags_risk_and_takes_earliest() -> None:
    techs = [_tech("TECH-01", [(2, 10, 0)]), _tech("TECH-02", [(3, 8, 0)])]
    choice = _choose(techs, prior=["TECH-02"], sla_hours=4)
    assert choice.technician_id == "TECH-01"  # earliest wins once the SLA is already missed
    assert choice.sla_risk
    assert "SLA at risk" in choice.reason


def test_technician_waits_for_parts() -> None:
    techs = [_tech("TECH-01", [(0, 10, 0), (2, 10, 0)])]
    choice = _choose(techs, earliest=NOW + timedelta(days=2), sla_hours=72)
    assert choice.start == _at(2, 10)


def test_technician_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        _choose([], est_hours=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        _choose([], earliest=datetime(2026, 10, 1, 9))
