"""Deterministic seed data for the mock enterprise systems.

All randomness comes from one ``random.Random(RANDOM_SEED)`` and every date is an
offset from a reference "today", so the same reference date always yields the
same database. Pin it with ``SEED_TODAY=YYYY-MM-DD`` (or ``--today``) for evals.

Usage::

    python -m app.seed --reset            # wipe and reseed
    python -m app.seed --if-empty         # seed only a fresh database (container start)
"""

import argparse
import random
from datetime import date, time, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import now_lk, settings
from app.db import SessionLocal
from app.models import (
    ID_SEQUENCES,
    Asset,
    Availability,
    Base,
    Customer,
    FaultCode,
    Part,
    ServiceHistory,
    Technician,
    Vendor,
    VendorPart,
)

RANDOM_SEED = 42
SLOT_STARTS = [time(8), time(10), time(12), time(14), time(16)]
AVAILABILITY_DAYS = 14
HISTORY_TARGET = 120

REGION_SITES: dict[str, list[str]] = {
    "Colombo": ["Colombo 3", "Colombo 7", "Nugegoda", "Dehiwala", "Rajagiriya", "Wattala"],
    "Kandy": ["Kandy City", "Peradeniya", "Katugastota", "Digana"],
    "Galle": ["Galle Fort", "Unawatuna", "Hikkaduwa", "Karapitiya"],
    "Kurunegala": ["Kurunegala Town", "Dambulla", "Kuliyapitiya", "Wariyapola"],
}

# model -> (asset kind, warranty years)
MODELS: dict[str, tuple[str, int]] = {
    "AP-500": ("Freezer", 2),  # ArcticPro AP-500 upright freezer
    "AP-900": ("Freezer", 2),  # ArcticPro AP-900 twin-door freezer
    "FL-140": ("Freezer", 2),  # FrostLine FL-140 island display freezer
    "PM-220": ("Chiller", 2),  # PolarMax PM-220 multideck chiller
    "CC-75": ("Chiller", 3),  # CoolCore CC-75 walk-in chiller
}
FREEZERS = ["AP-500", "AP-900", "FL-140"]
ALL_MODELS = list(MODELS)

# id, name, region, tier, email
CUSTOMERS: list[tuple[str, str, str, str, str]] = [
    ("CUST-001", "FreshMart", "Colombo", "gold", "ops@freshmart.lk"),
    ("CUST-002", "Lanka Super Stores", "Colombo", "gold", "facilities@lankasuper.lk"),
    ("CUST-003", "Green Basket", "Colombo", "silver", "maintenance@greenbasket.lk"),
    ("CUST-004", "City Grocers", "Colombo", "bronze", "admin@citygrocers.lk"),
    ("CUST-005", "Harbour Foods", "Colombo", "silver", "service@harbourfoods.lk"),
    ("CUST-006", "Hill Country Mart", "Kandy", "gold", "ops@hillcountrymart.lk"),
    ("CUST-007", "Temple Road Super", "Kandy", "silver", "manager@templeroadsuper.lk"),
    ("CUST-008", "Peradeniya Fresh", "Kandy", "bronze", "info@peradeniyafresh.lk"),
    ("CUST-009", "Kandy Cold Store", "Kandy", "silver", "ops@kandycold.lk"),
    ("CUST-010", "Lakeside Market", "Kandy", "bronze", "hello@lakesidemarket.lk"),
    ("CUST-011", "Fort Fresh", "Galle", "gold", "facilities@fortfresh.lk"),
    ("CUST-012", "Southern Super", "Galle", "silver", "ops@southernsuper.lk"),
    ("CUST-013", "Unawatuna Grocers", "Galle", "bronze", "info@unawatunagrocers.lk"),
    ("CUST-014", "Ocean Catch Fisheries", "Galle", "gold", "plant@oceancatch.lk"),
    ("CUST-015", "Coastline Mart", "Galle", "bronze", "admin@coastlinemart.lk"),
    ("CUST-016", "Wayamba Super", "Kurunegala", "silver", "ops@wayambasuper.lk"),
    ("CUST-017", "Kurunegala Food City", "Kurunegala", "gold", "facilities@kfoodcity.lk"),
    ("CUST-018", "Rock Hill Grocers", "Kurunegala", "bronze", "info@rockhillgrocers.lk"),
    ("CUST-019", "Dambulla Produce", "Kurunegala", "silver", "ops@dambullaproduce.lk"),
    ("CUST-020", "North Western Dairy", "Kurunegala", "bronze", "plant@nwdairy.lk"),
]
DEMO_CUSTOMER_ID = "CUST-001"
DEMO_ASSET_ID = "FRZ-1043"

# id, name, region, skills
TECHNICIANS: list[tuple[str, str, str, list[str]]] = [
    ("TECH-01", "Nimal Perera", "Colombo", ["refrigeration", "electrical"]),
    ("TECH-02", "Kasun Fernando", "Colombo", ["refrigeration", "electrical", "general"]),
    ("TECH-03", "Dilani Jayasinghe", "Colombo", ["electrical", "general"]),
    ("TECH-04", "Ruwan Bandara", "Kandy", ["refrigeration", "general"]),
    ("TECH-05", "Ishara Wickramasinghe", "Kandy", ["electrical", "general"]),
    ("TECH-06", "Chamara Silva", "Galle", ["refrigeration", "electrical"]),
    ("TECH-07", "Tharindu de Mel", "Galle", ["electrical", "general"]),
    ("TECH-08", "Sanjeewa Rathnayake", "Kurunegala", ["refrigeration", "electrical", "general"]),
]
# Kurunegala's only technician is fully booked for the first few days so evals
# can exercise the "no technician available within SLA" path.
BUSY_TECHNICIAN_ID = "TECH-08"
BUSY_DAYS = 5

# code, name, symptoms, models, likely parts, skill, est hours
FAULT_CODES: list[tuple[str, str, list[str], list[str], list[str], str, float]] = [
    ("COMP_FAIL", "Compressor failure",
     ["not cooling", "compressor not starting", "loud clicking from compressor", "temperature rising"],
     ALL_MODELS, ["CMP-AP", "CMP-FL", "CMP-PM", "CMP-CC"], "refrigeration", 4),
    ("REF_LEAK", "Refrigerant leak",
     ["gradual loss of cooling", "oil stains near pipes", "hissing sound", "ice on evaporator coil"],
     ALL_MODELS, ["GAS-R404A", "GAS-R290", "DRY-FILTER"], "refrigeration", 3),
    ("EVAP_FAN", "Evaporator fan motor failure",
     ["uneven cooling", "no airflow inside cabinet", "fan noise inside", "frost build-up on coil"],
     ALL_MODELS, ["FAN-EVAP-S", "FAN-EVAP-L"], "electrical", 2),
    ("COND_FAN", "Condenser fan failure",
     ["unit running hot", "compressor cycling frequently", "warm air not expelled", "rattling at the back"],
     ALL_MODELS, ["FAN-COND-S", "FAN-COND-L"], "electrical", 2),
    ("THERMO_FAIL", "Thermostat failure",
     ["temperature erratic", "runs constantly", "does not start a cooling cycle", "set point ignored"],
     ALL_MODELS, ["THM-MECH", "THM-DIGI"], "electrical", 1.5),
    ("DEFROST_HEATER", "Defrost heater failure",
     ["heavy ice build-up on coils", "freezer warming gradually", "defrost cycle not clearing ice"],
     FREEZERS, ["HTR-DEF-AP", "HTR-DEF-FL"], "electrical", 2),
    ("DEFROST_TIMER", "Defrost timer failure",
     ["ice build-up", "unit stuck in defrost", "never goes into defrost"],
     FREEZERS, ["TMR-DEF"], "electrical", 1),
    ("DOOR_GASKET", "Door gasket worn",
     ["door not sealing", "condensation around door", "frost near the door", "cold air leaking"],
     ["AP-500", "AP-900", "CC-75"], ["GSK-AP500", "GSK-AP900", "GSK-CC75"], "general", 1),
    ("CTRL_BOARD", "Control board fault",
     ["display blank", "error codes on display", "unit unresponsive", "random shutdowns"],
     ALL_MODELS, ["CTL-BRD-A", "CTL-BRD-B"], "electrical", 3),
    ("TEMP_SENSOR", "Temperature sensor fault",
     ["display shows wrong temperature", "temperature alarm going off", "readings jumping"],
     ALL_MODELS, ["SNS-NTC"], "electrical", 1),
    ("DRAIN_BLOCK", "Blocked or frozen drain",
     ["water leaking on floor", "water pooling inside", "ice in drain pan"],
     ALL_MODELS, ["HTR-DRAIN"], "general", 1),
    ("START_RELAY", "Start relay or capacitor failure",
     ["compressor hums but does not start", "clicking every few minutes", "breaker tripping"],
     ALL_MODELS, ["RLY-START", "CAP-START"], "electrical", 1.5),
]  # fmt: skip

# sku, name, compatible models, unit cost LKR, stock, reorder level
PARTS: list[tuple[str, str, list[str], int, int, int]] = [
    ("CMP-AP", "Compressor 1/2 HP (ArcticPro)", ["AP-500", "AP-900"], 92_000, 2, 1),
    ("CMP-FL", "Compressor 3/4 HP (FrostLine)", ["FL-140"], 118_000, 0, 1),
    ("CMP-PM", "Compressor 1 HP (PolarMax)", ["PM-220"], 135_000, 1, 1),
    ("CMP-CC", "Scroll compressor 2 HP (CoolCore)", ["CC-75"], 210_000, 0, 1),
    ("RLY-START", "Compressor start relay", ALL_MODELS, 4_500, 25, 10),
    ("CAP-START", "Compressor start capacitor", ALL_MODELS, 3_200, 0, 10),
    ("GAS-R404A", "Refrigerant R404A, 10 kg cylinder", ["AP-500", "AP-900", "PM-220", "CC-75"], 38_000, 6, 3),
    ("GAS-R290", "Refrigerant R290, 5 kg cylinder", ["FL-140"], 24_000, 3, 2),
    ("DRY-FILTER", "Filter drier", ALL_MODELS, 2_800, 0, 10),
    ("FAN-EVAP-S", "Evaporator fan motor, small", ["AP-500", "FL-140"], 14_500, 4, 2),
    ("FAN-EVAP-L", "Evaporator fan motor, large", ["AP-900", "PM-220", "CC-75"], 22_000, 0, 2),
    ("FAN-COND-S", "Condenser fan motor, small", ["AP-500", "AP-900", "FL-140"], 16_000, 3, 2),
    ("FAN-COND-L", "Condenser fan motor, large", ["PM-220", "CC-75"], 27_500, 0, 2),
    ("THM-MECH", "Mechanical thermostat", ["AP-500", "AP-900"], 6_500, 10, 4),
    ("THM-DIGI", "Digital temperature controller", ["FL-140", "PM-220", "CC-75"], 18_000, 5, 2),
    ("HTR-DEF-AP", "Defrost heater element (ArcticPro)", ["AP-500", "AP-900"], 12_000, 0, 2),
    ("HTR-DEF-FL", "Defrost heater element (FrostLine)", ["FL-140"], 13_500, 2, 2),
    ("TMR-DEF", "Defrost timer", FREEZERS, 7_800, 6, 3),
    ("GSK-AP500", "Door gasket (AP-500)", ["AP-500"], 5_500, 8, 4),
    ("GSK-AP900", "Door gasket (AP-900)", ["AP-900"], 7_200, 0, 4),
    ("GSK-CC75", "Walk-in door gasket (CC-75)", ["CC-75"], 15_000, 2, 1),
    ("CTL-BRD-A", "Control board type A", FREEZERS, 48_000, 1, 1),
    ("CTL-BRD-B", "Control board type B", ["PM-220", "CC-75"], 112_000, 0, 1),
    ("SNS-NTC", "NTC temperature sensor", ALL_MODELS, 2_500, 30, 10),
    ("HTR-DRAIN", "Drain line heater", ALL_MODELS, 4_800, 7, 3),
    ("EXP-VALVE", "Thermostatic expansion valve", ["PM-220", "CC-75"], 26_000, 2, 1),
    ("CND-COIL-PM", "Condenser coil (PM-220)", ["PM-220"], 74_000, 0, 1),
    ("EVP-COIL-CC", "Evaporator coil (CC-75)", ["CC-75"], 165_000, 0, 1),
    ("LED-STRIP", "Canopy LED strip", ["PM-220", "FL-140"], 3_900, 12, 5),
    ("NIGHT-BLIND", "Night blind assembly", ["PM-220"], 19_500, 3, 1),
]

# id, name, approved, lead time days, price factor vs unit cost
VENDORS: list[tuple[str, str, bool, int, float]] = [
    ("VEN-01", "Ceylon Refrigeration Supplies", True, 2, 1.10),
    ("VEN-02", "Colombo Cooling Parts", True, 1, 1.20),
    ("VEN-03", "Lanka Industrial Spares", True, 5, 0.98),
    ("VEN-04", "Southern Compressor Co.", True, 3, 1.05),
    ("VEN-05", "QuickParts Trading", False, 1, 0.90),
    ("VEN-06", "Asia Grey Imports", False, 7, 0.85),
]


def _round_100(value: float) -> int:
    return int(round(value / 100.0)) * 100


def _build_assets(rng: random.Random, today: date) -> list[Asset]:
    """50 assets. FreshMart's four are fixed; the other 46 are spread over 19 customers."""
    freshmart = [
        ("FRZ-1041", "Freezer #1", "AP-500", "Colombo 7", 1_200),
        ("FRZ-1042", "Freezer #2", "AP-900", "Colombo 7", 900),
        (DEMO_ASSET_ID, "Freezer #3", "AP-500", "Colombo 7", 420),
        ("CHL-1044", "Chiller #1", "PM-220", "Nugegoda", 1_500),
    ]
    assets: list[Asset] = []
    for asset_id, name, model, site, age_days in freshmart:
        install = today - timedelta(days=age_days)
        assets.append(
            Asset(
                id=asset_id, customer_id=DEMO_CUSTOMER_ID, name=name, model=model,
                serial=f"SN-{model}-{asset_id[-4:]}", site=site, region="Colombo",
                install_date=install, warranty_end=install + timedelta(days=365 * MODELS[model][1]),
            )
        )  # fmt: skip

    others = CUSTOMERS[1:]
    counts = [2] * len(others)
    for i in rng.sample(range(len(others)), 8):
        counts[i] = 3
    numbers = iter([n for n in range(1001, 1051) if not 1041 <= n <= 1044])
    for (customer_id, _, region, _, _), count in zip(others, counts, strict=True):
        sites = rng.sample(REGION_SITES[region], rng.choice([1, 2]))
        seen: dict[tuple[str, str], int] = {}
        for _ in range(count):
            site = rng.choice(sites)
            model = rng.choice(ALL_MODELS)
            kind, warranty_years = MODELS[model]
            seen[(site, kind)] = seen.get((site, kind), 0) + 1
            number = next(numbers)
            asset_id = f"{'FRZ' if kind == 'Freezer' else 'CHL'}-{number}"
            install = today - timedelta(days=rng.randint(180, 3_300))
            assets.append(
                Asset(
                    id=asset_id, customer_id=customer_id, name=f"{kind} #{seen[(site, kind)]}",
                    model=model, serial=f"SN-{model}-{number}", site=site, region=region,
                    install_date=install, warranty_end=install + timedelta(days=365 * warranty_years),
                )
            )  # fmt: skip
    return assets


def _history_record(
    rng: random.Random,
    asset: Asset,
    when: date,
    faults: list[FaultCode],
    parts: dict[str, Part],
    techs_by_region: dict[str, list[str]],
    fault: FaultCode | None = None,
) -> ServiceHistory:
    fault = fault or rng.choice([f for f in faults if asset.model in f.applies_to_models])
    compatible = [s for s in fault.likely_parts if asset.model in parts[s].compatible_models]
    parts_used = [{"sku": rng.choice(compatible), "qty": 1}] if compatible else []
    return ServiceHistory(
        asset_id=asset.id,
        date=when,
        fault_code=fault.code,
        notes=f"{fault.name} diagnosed and repaired.",
        parts_used=parts_used,
        technician_id=rng.choice(techs_by_region[asset.region]),
    )


def _build_history(
    rng: random.Random,
    today: date,
    assets: list[Asset],
    faults: list[FaultCode],
    parts: dict[str, Part],
) -> list[ServiceHistory]:
    techs_by_region: dict[str, list[str]] = {}
    for tech_id, _, region, _ in TECHNICIANS:
        techs_by_region.setdefault(region, []).append(tech_id)

    # The demo case: FRZ-1043's compressor was replaced 60 days ago by TECH-02.
    records = [
        ServiceHistory(
            asset_id=DEMO_ASSET_ID, date=today - timedelta(days=300), fault_code="THERMO_FAIL",
            notes="Thermostat replaced; set point recalibrated.",
            parts_used=[{"sku": "THM-MECH", "qty": 1}], technician_id="TECH-01",
        ),
        ServiceHistory(
            asset_id=DEMO_ASSET_ID, date=today - timedelta(days=60), fault_code="COMP_FAIL",
            notes="Compressor seized and was replaced. Customer advised to keep the condenser clear.",
            parts_used=[{"sku": "CMP-AP", "qty": 1}], technician_id="TECH-02",
        ),
    ]  # fmt: skip

    candidates = [a for a in assets if a.customer_id != DEMO_CUSTOMER_ID]

    # Six assets with a repeat failure: the same fault twice, under 90 days apart,
    # the latest one recent enough that a new complaint is a repeat failure too.
    repeat_assets = rng.sample(candidates, 6)
    for asset in repeat_assets:
        fault = rng.choice([f for f in faults if asset.model in f.applies_to_models])
        latest = today - timedelta(days=rng.randint(15, 85))
        earlier = latest - timedelta(days=rng.randint(30, 80))
        for when in (earlier, latest):
            records.append(_history_record(rng, asset, when, faults, parts, techs_by_region, fault))

    # Older background history, at least 120 days back so it never trips the
    # repeat-failure rule by accident.
    while len(records) < HISTORY_TARGET:
        asset = rng.choice(candidates)
        first, last = asset.install_date + timedelta(days=30), today - timedelta(days=120)
        if first > last:
            continue
        when = first + timedelta(days=rng.randint(0, (last - first).days))
        records.append(_history_record(rng, asset, when, faults, parts, techs_by_region))

    records.sort(key=lambda r: (r.date, r.asset_id))
    return records


def _build_availability(rng: random.Random, today: date) -> list[Availability]:
    slots: list[Availability] = []
    for offset in range(AVAILABILITY_DAYS):
        day = today + timedelta(days=offset)
        if day.weekday() == 6:  # no Sunday shifts
            continue
        for tech_id, *_ in TECHNICIANS:
            for start in SLOT_STARTS:
                fully_booked = tech_id == BUSY_TECHNICIAN_ID and offset < BUSY_DAYS
                booked = fully_booked or rng.random() < 0.3
                slots.append(
                    Availability(
                        technician_id=tech_id, date=day, start=start,
                        end=time(start.hour + 2), booked=booked,
                    )
                )  # fmt: skip
    return slots


def build_seed(today: date) -> list[Any]:
    """Every seed row, in insert order. Pure: same ``today`` -> same rows."""
    rng = random.Random(RANDOM_SEED)

    customers = [
        Customer(id=c, name=n, region=r, contract_tier=t, contact_email=e) for c, n, r, t, e in CUSTOMERS
    ]
    technicians = [Technician(id=i, name=n, region=r, skills=s) for i, n, r, s in TECHNICIANS]
    faults = [
        FaultCode(
            code=c, name=n, symptoms=s, applies_to_models=m, likely_parts=p, skill_required=k, est_hours=h
        )
        for c, n, s, m, p, k, h in FAULT_CODES
    ]
    parts = {
        sku: Part(sku=sku, name=n, compatible_models=m, unit_cost_lkr=c, stock_qty=q, reorder_level=r)
        for sku, n, m, c, q, r in PARTS
    }
    vendors = [Vendor(id=i, name=n, approved=a, lead_time_days=d) for i, n, a, d, _ in VENDORS]

    # Every part has the slow, cheap approved vendor (VEN-03) and the unapproved
    # cut-price one (VEN-05), plus one or two of the faster approved vendors.
    factors = {i: f for i, _, _, _, f in VENDORS}
    vendor_parts: list[VendorPart] = []
    for sku, part in parts.items():
        supplier_ids = {"VEN-03", "VEN-05", *rng.sample(["VEN-01", "VEN-02", "VEN-04"], rng.choice([1, 2]))}
        if rng.random() < 0.5:
            supplier_ids.add("VEN-06")
        for vendor_id in sorted(supplier_ids):
            price = _round_100(part.unit_cost_lkr * factors[vendor_id] * rng.uniform(0.97, 1.03))
            vendor_parts.append(VendorPart(vendor_id=vendor_id, sku=sku, price_lkr=price))

    assets = _build_assets(rng, today)
    history = _build_history(rng, today, assets, faults, parts)
    availability = _build_availability(rng, today)

    return [
        *customers, *technicians, *faults, *parts.values(), *vendors, *vendor_parts,
        *assets, *history, *availability,
    ]  # fmt: skip


def reset(session: Session) -> None:
    """Empty every table and restart the id sequences."""
    tables = ", ".join(f'{t.schema}."{t.name}"' for t in Base.metadata.sorted_tables)
    session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    for seq in ID_SEQUENCES:
        session.execute(text(f"ALTER SEQUENCE {seq.schema}.{seq.name} RESTART WITH 1"))


def seed(session: Session, today: date) -> None:
    """Wipe the enterprise tables and load the seed for ``today``."""
    reset(session)
    rows = build_seed(today)
    # Insert table by table, parents first, so foreign keys are satisfied.
    for model in (
        Customer,
        Technician,
        FaultCode,
        Part,
        Vendor,
        VendorPart,
        Asset,
        ServiceHistory,
        Availability,
    ):
        session.add_all([r for r in rows if isinstance(r, model)])
        session.flush()
    session.commit()


def resolve_today(cli_value: str | None = None) -> date:
    if cli_value:
        return date.fromisoformat(cli_value)
    return settings.seed_today or now_lk().date()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the mock enterprise database.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--reset", action="store_true", help="wipe all data and reseed")
    mode.add_argument("--if-empty", action="store_true", help="seed only when no customers exist")
    parser.add_argument("--today", help="reference date YYYY-MM-DD (default: SEED_TODAY or today)")
    args = parser.parse_args()

    today = resolve_today(args.today)
    with SessionLocal() as session:
        if args.if_empty and session.scalar(select(func.count()).select_from(Customer)):
            print("Seed skipped: database already has data.")
            return
        seed(session, today)
    print(f"Seeded enterprise data for reference date {today.isoformat()}.")


if __name__ == "__main__":
    main()
