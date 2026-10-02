"""Seed volumes, spread and determinism."""

from datetime import date, timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    Asset,
    Availability,
    Customer,
    FaultCode,
    Part,
    ServiceHistory,
    Technician,
    Vendor,
    VendorPart,
)
from app.seed import build_seed
from tests.conftest import SEED_TODAY


def _count(model: type) -> int:
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_seed_volumes() -> None:
    assert _count(Customer) == 20
    assert _count(Asset) == 50
    assert _count(ServiceHistory) == 120
    assert _count(FaultCode) == 12
    assert _count(Part) == 30
    assert _count(Vendor) == 6
    assert _count(Technician) == 8


def test_seed_spread() -> None:
    with SessionLocal() as session:
        regions = set(session.scalars(select(Customer.region)))
        models = set(session.scalars(select(Asset.model)))
        approved = list(session.scalars(select(Vendor.approved)))
        zero_stock = session.scalar(select(func.count()).select_from(Part).where(Part.stock_qty == 0))
        dates = list(session.scalars(select(Availability.date).distinct()))
    assert regions == {"Colombo", "Kandy", "Galle", "Kurunegala"}
    assert len(models) == 5
    assert approved.count(True) == 4 and approved.count(False) == 2
    assert zero_stock >= 5
    assert min(dates) == SEED_TODAY
    assert max(dates) == SEED_TODAY + timedelta(days=13)


def test_every_part_has_an_approved_vendor() -> None:
    with SessionLocal() as session:
        supplied = session.scalars(
            select(VendorPart.sku).join(Vendor, Vendor.id == VendorPart.vendor_id).where(Vendor.approved)
        )
        assert set(supplied) == set(session.scalars(select(Part.sku)))


def test_repeat_failures_within_90_days_exist() -> None:
    with SessionLocal() as session:
        records = session.execute(
            select(ServiceHistory.asset_id, ServiceHistory.date).order_by(ServiceHistory.date)
        ).all()
    last_seen: dict[str, date] = {}
    repeat_assets: set[str] = set()
    for asset_id, when in records:
        if asset_id in last_seen and (when - last_seen[asset_id]).days <= 90:
            repeat_assets.add(asset_id)
        last_seen[asset_id] = when
    assert len(repeat_assets) >= 5


def _snapshot(rows: list) -> list[tuple]:
    return [
        (type(row).__name__, tuple((c.key, repr(getattr(row, c.key))) for c in row.__table__.columns))
        for row in rows
    ]


def test_seed_is_deterministic() -> None:
    assert _snapshot(build_seed(SEED_TODAY)) == _snapshot(build_seed(SEED_TODAY))


def test_seed_dates_move_with_reference_date() -> None:
    first = {r.id: r for r in build_seed(SEED_TODAY) if isinstance(r, Asset)}
    shifted = {r.id: r for r in build_seed(SEED_TODAY + timedelta(days=7)) if isinstance(r, Asset)}
    assert first.keys() == shifted.keys()
    for asset_id, asset in first.items():
        assert shifted[asset_id].install_date - asset.install_date == timedelta(days=7)
        assert shifted[asset_id].name == asset.name
