"""CoolTech business rules as pure functions.

Every threshold comes from ``config/rules.yaml``. Nothing here calls the clock,
the network or an LLM: callers pass ``now`` / ``today`` and the config, so each
rule is deterministic and unit-testable. Money is whole LKR.
"""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from app.config import settings
from app.enterprise import (
    Asset,
    ContractTier,
    PartNeed,
    Priority,
    ServiceRecord,
    TechnicianAvailability,
    VendorOffer,
)

TIERS: tuple[ContractTier, ...] = ("gold", "silver", "bronze")
PRIORITIES: tuple[Priority, ...] = ("P1", "P2", "P3")


# --- configuration -------------------------------------------------------------


class RulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sla_hours: dict[ContractTier, PositiveInt]
    po_approval_threshold_lkr: int = Field(ge=0)
    repeat_failure_window_days: PositiveInt
    diagnosis_min_confidence: float = Field(ge=0, le=1)
    inspection_hours: float = Field(gt=0, le=10)

    @model_validator(mode="after")
    def _every_tier_has_an_sla(self) -> "RulesConfig":
        missing = set(TIERS) - set(self.sla_hours)
        if missing:
            raise ValueError(f"sla_hours is missing tiers: {sorted(missing)}")
        return self


def load_rules(path: Path | str | None = None) -> RulesConfig:
    with open(path or settings.rules_path, encoding="utf-8") as f:
        return RulesConfig.model_validate(yaml.safe_load(f))


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


# --- SLA and priority ----------------------------------------------------------


def sla_deadline(created_at: datetime, tier: ContractTier, cfg: RulesConfig) -> datetime:
    """Latest time a technician must be on site, by contract tier."""
    _require_aware(created_at, "created_at")
    return created_at + timedelta(hours=cfg.sla_hours[tier])


def priority(tier: ContractTier, asset_down: bool, repeat_failure: bool) -> Priority:
    """Gold + asset down = P1; other gold and silver = P2; bronze = P3.

    A repeat failure raises the priority one level (P1 is the ceiling).
    """
    if tier == "gold":
        base: Priority = "P1" if asset_down else "P2"
    elif tier == "silver":
        base = "P2"
    else:
        base = "P3"
    level = PRIORITIES.index(base)
    if repeat_failure:
        level = max(level - 1, 0)
    return PRIORITIES[level]


# --- asset facts and ticket notes ----------------------------------------------


class RecentFault(BaseModel):
    date: date
    fault_code: str
    technician_id: str


class AssetFacts(BaseModel):
    under_warranty: bool
    warranty_end: date
    repeat_failure_90d: bool = Field(description="Any repair within the repeat-failure window")
    age_years: float
    last_service_date: date | None
    last_fault_code: str | None
    last_technician_id: str | None
    recent_faults: list[RecentFault] = Field(description="Repairs within the window, newest first")
    previous_technician_ids: list[str] = Field(description="Everyone who has worked on it, newest first")


def compute_asset_facts(
    asset: Asset, history: list[ServiceRecord], today: date, cfg: RulesConfig
) -> AssetFacts:
    past = sorted((r for r in history if r.date <= today), key=lambda r: (r.date, r.id), reverse=True)
    window_start = today - timedelta(days=cfg.repeat_failure_window_days)
    recent = [r for r in past if r.date >= window_start]
    previous: list[str] = []
    for record in past:
        if record.technician_id not in previous:
            previous.append(record.technician_id)
    last = past[0] if past else None
    return AssetFacts(
        under_warranty=today <= asset.warranty_end,
        warranty_end=asset.warranty_end,
        repeat_failure_90d=bool(recent),
        age_years=round((today - asset.install_date).days / 365.25, 1),
        last_service_date=last.date if last else None,
        last_fault_code=last.fault_code if last else None,
        last_technician_id=last.technician_id if last else None,
        recent_faults=[
            RecentFault(date=r.date, fault_code=r.fault_code, technician_id=r.technician_id) for r in recent
        ],
        previous_technician_ids=previous,
    )


def ticket_notes(facts: AssetFacts) -> list[str]:
    """Notes the warranty and repeat-failure rules add to the CRM ticket."""
    notes = []
    if facts.under_warranty:
        notes.append(
            f"Warranty claim: asset is under warranty until {facts.warranty_end.isoformat()}; "
            "parts will be claimed under warranty."
        )
    if facts.repeat_failure_90d:
        notes.append(
            f"Supervisor review: repeat failure. Last repair {facts.last_service_date} "
            f"({facts.last_fault_code}) by {facts.last_technician_id}."
        )
    return notes


# --- diagnosis -----------------------------------------------------------------


def needs_inspection(confidence: float, cfg: RulesConfig) -> bool:
    """Below the minimum confidence, book an inspection visit with no parts or PO."""
    return confidence < cfg.diagnosis_min_confidence


# --- parts ---------------------------------------------------------------------


class PartPlan(BaseModel):
    sku: str
    qty: int
    stock_qty: int
    status: Literal["in_stock", "reserved", "needs_po"]


def plan_parts(required: list[PartNeed], stock: dict[str, int]) -> list[PartPlan]:
    """Mark each needed part in_stock (enough on hand for the full quantity) or needs_po.

    Duplicate skus are merged. Partial stock is not split: the whole quantity is ordered.
    """
    totals: dict[str, int] = {}
    for need in required:
        totals[need.sku] = totals.get(need.sku, 0) + need.qty
    plan = []
    for sku, qty in totals.items():
        on_hand = stock.get(sku, 0)
        plan.append(
            PartPlan(sku=sku, qty=qty, stock_qty=on_hand, status="in_stock" if on_hand >= qty else "needs_po")
        )
    return plan


# --- procurement ---------------------------------------------------------------


class QuotedLine(BaseModel):
    sku: str
    qty: int
    unit_price_lkr: int


class VendorChoice(BaseModel):
    vendor_id: str
    vendor_name: str
    lines: list[QuotedLine]
    total_lkr: int
    lead_time_days: int
    parts_ready_at: datetime
    sla_risk: bool
    reason: str


def parts_ready_at(now: datetime, lead_time_days: int) -> datetime:
    _require_aware(now, "now")
    return now + timedelta(days=lead_time_days)


def choose_vendor(
    lines: list[PartNeed], offers: list[VendorOffer], now: datetime, sla_deadline: datetime
) -> VendorChoice | None:
    """Approved vendors that supply every line; cheapest among those meeting the SLA.

    If none meets the SLA, the fastest is chosen and ``sla_risk`` is set.
    Returns None when no single approved vendor can supply every line.
    """
    _require_aware(sla_deadline, "sla_deadline")
    if not lines:
        return None
    by_vendor: dict[str, dict[str, VendorOffer]] = {}
    for offer in offers:
        if offer.approved:
            by_vendor.setdefault(offer.vendor_id, {})[offer.sku] = offer

    candidates: list[VendorChoice] = []
    for vendor_id, vendor_offers in sorted(by_vendor.items()):
        if any(line.sku not in vendor_offers for line in lines):
            continue
        quoted = [
            QuotedLine(sku=line.sku, qty=line.qty, unit_price_lkr=vendor_offers[line.sku].price_lkr)
            for line in lines
        ]
        lead = max(vendor_offers[line.sku].lead_time_days for line in lines)
        ready = parts_ready_at(now, lead)
        candidates.append(
            VendorChoice(
                vendor_id=vendor_id,
                vendor_name=next(iter(vendor_offers.values())).vendor_name,
                lines=quoted,
                total_lkr=sum(q.unit_price_lkr * q.qty for q in quoted),
                lead_time_days=lead,
                parts_ready_at=ready,
                sla_risk=ready > sla_deadline,
                reason="",
            )
        )
    if not candidates:
        return None

    on_time = [c for c in candidates if not c.sla_risk]
    if on_time:
        best = min(on_time, key=lambda c: (c.total_lkr, c.lead_time_days, c.vendor_id))
        best.reason = (
            f"Cheapest of {len(on_time)} approved vendor(s) that deliver before the SLA deadline "
            f"(LKR {best.total_lkr:,}, {best.lead_time_days} day lead time)."
        )
    else:
        best = min(candidates, key=lambda c: (c.lead_time_days, c.total_lkr, c.vendor_id))
        best.reason = (
            f"No approved vendor delivers before the SLA deadline; chose the fastest "
            f"({best.lead_time_days} day lead time, LKR {best.total_lkr:,}). SLA at risk."
        )
    return best


def po_requires_approval(total_lkr: int, cfg: RulesConfig) -> bool:
    """Above the threshold a manager must approve; at or below it is auto-approved."""
    return total_lkr > cfg.po_approval_threshold_lkr


# --- scheduling ----------------------------------------------------------------


class TechnicianChoice(BaseModel):
    technician_id: str
    technician_name: str
    start: datetime
    end: datetime
    slot_ids: list[int]
    prior_work_on_asset: bool
    jobs_that_day: int
    sla_risk: bool
    reason: str


def _feasible_visits(tech: TechnicianAvailability, earliest: datetime, est_hours: float):
    """Yield (start, end, slot_ids, jobs_that_day) for each start where the job fits in
    back-to-back open slots that all begin at or after ``earliest``."""
    slots = sorted(tech.slots, key=lambda s: s.start)
    for i, first in enumerate(slots):
        if first.start < earliest:
            continue
        chain = [first]
        hours = (first.end - first.start).total_seconds() / 3600
        j = i + 1
        while hours < est_hours and j < len(slots) and slots[j].start == chain[-1].end:
            chain.append(slots[j])
            hours += (slots[j].end - slots[j].start).total_seconds() / 3600
            j += 1
        if hours >= est_hours:
            yield first.start, chain[-1].end, [s.slot_id for s in chain], first.jobs_that_day


def choose_technician(
    candidates: list[TechnicianAvailability],
    *,
    region: str,
    skill: str,
    prior_technician_ids: list[str],
    earliest: datetime,
    sla_deadline: datetime,
    est_hours: float,
) -> TechnicianChoice | None:
    """Pick the technician and visit start.

    Eligible: has ``skill`` and works in ``region``. Among visits starting by the SLA
    deadline, rank by previous work on this asset (``prior_technician_ids`` is newest
    first, so the technician who worked on it most recently ranks highest), then
    earliest start, then fewest jobs that day. If no visit meets the SLA, take the
    earliest one and flag ``sla_risk``.
    """
    _require_aware(earliest, "earliest")
    _require_aware(sla_deadline, "sla_deadline")
    if est_hours <= 0:
        raise ValueError("est_hours must be positive")

    prior_rank = {tech_id: i for i, tech_id in enumerate(prior_technician_ids)}
    no_prior = len(prior_rank)

    def recency(option: TechnicianChoice) -> int:
        return prior_rank.get(option.technician_id, no_prior)

    options: list[TechnicianChoice] = []
    for tech in candidates:
        if tech.region.lower() != region.lower() or skill.lower() not in {s.lower() for s in tech.skills}:
            continue
        for start, end, slot_ids, jobs in _feasible_visits(tech, earliest, est_hours):
            options.append(
                TechnicianChoice(
                    technician_id=tech.technician_id,
                    technician_name=tech.name,
                    start=start,
                    end=end,
                    slot_ids=slot_ids,
                    prior_work_on_asset=tech.technician_id in prior_rank,
                    jobs_that_day=jobs,
                    sla_risk=start > sla_deadline,
                    reason="",
                )
            )
    if not options:
        return None

    on_time = [o for o in options if not o.sla_risk]
    if on_time:
        best = min(
            on_time,
            key=lambda o: (recency(o), o.start, o.jobs_that_day, o.technician_id),
        )
        why = "worked on this asset most recently" if best.prior_work_on_asset else "earliest eligible slot"
        if best.prior_work_on_asset and prior_rank[best.technician_id] > 0:
            why = "has worked on this asset before"
        best.reason = (
            f"{best.technician_name} ({best.technician_id}) can be on site within the SLA and {why}."
        )
    else:
        best = min(
            options,
            key=lambda o: (o.start, recency(o), o.jobs_that_day, o.technician_id),
        )
        best.reason = (
            f"No eligible technician can be on site before the SLA deadline; "
            f"{best.technician_name} ({best.technician_id}) has the earliest slot. SLA at risk."
        )
    return best
