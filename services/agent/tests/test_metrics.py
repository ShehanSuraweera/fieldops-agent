"""KPI definitions for the dashboard."""

from datetime import datetime, timedelta

from app.config import LK_TZ
from app.metrics import compute_metrics
from app.store import RunRecord

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=LK_TZ)


def _run(status: str, seconds: float = 10, tokens: int = 1000, **summary) -> RunRecord:
    return RunRecord(
        id=f"run_{status}_{seconds}_{tokens}", ticket_id=None, customer_email="a@b.lk", raw_text="x",
        status=status, clock=T0, created_at=T0, finished_at=T0 + timedelta(seconds=seconds),
        input_tokens=tokens - 100, output_tokens=100, summary={"status": status, **summary},
    )  # fmt: skip


def test_kpis() -> None:
    runs = [
        _run("scheduled", 4, 2000, approval_required=False),  # auto-resolved
        _run("scheduled", 6, 2000, approval_required=False),  # auto-resolved
        _run("scheduled", 50, 3000, approval_required=True, approval_decision="approve"),  # needed a manager
        _run("needs_manual_procurement", 8, 3000, approval_required=True, approval_decision="reject"),
        _run("needs_info", 2, 1000),
        _run("needs_human", 1, 0),
        _run("awaiting_approval", 3, 1500, approval_required=True),  # not finished: excluded
        _run("running", 0, 0),
    ]
    m = compute_metrics(runs)
    assert (m.total_runs, m.finished_runs, m.running, m.awaiting_approval) == (8, 6, 1, 1)
    assert m.auto_resolved_rate == round(2 / 6, 4)
    assert m.avg_time_to_schedule_s == 20.0  # (4 + 6 + 50) / 3, approval wait included
    assert m.approval_rate == 0.5
    assert m.approvals_decided == 2
    assert m.avg_tokens_per_run == round((2000 + 2000 + 3000 + 3000 + 1000 + 0) / 6, 2)
    assert m.by_status["scheduled"] == 3


def test_kpis_with_no_runs_are_empty_not_zero() -> None:
    m = compute_metrics([])
    assert m.auto_resolved_rate is None
    assert m.avg_time_to_schedule_s is None
    assert m.approval_rate is None
    assert m.avg_tokens_per_run is None
