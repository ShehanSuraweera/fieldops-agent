"""Dashboard KPIs, computed from the run log. Pure function so the definitions are testable."""

from collections import Counter

from pydantic import BaseModel, Field

from app.store import RunRecord

FINISHED = {"scheduled", "needs_info", "needs_manual_procurement", "needs_human"}


class Metrics(BaseModel):
    total_runs: int
    finished_runs: int
    running: int
    awaiting_approval: int
    by_status: dict[str, int]
    auto_resolved_rate: float | None = Field(
        description="Finished runs scheduled with no human involved (no PO approval, no escalation)"
    )
    avg_time_to_schedule_s: float | None = Field(
        description="Mean wall-clock seconds from run start to 'scheduled', including any approval wait"
    )
    approval_rate: float | None = Field(description="POs a manager approved / POs a manager decided")
    approvals_decided: int
    avg_tokens_per_run: float | None = Field(description="Mean input + output tokens over finished runs")


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def compute_metrics(runs: list[RunRecord]) -> Metrics:
    by_status = Counter(run.status for run in runs)
    finished = [run for run in runs if run.status in FINISHED]
    scheduled = [run for run in finished if run.status == "scheduled"]
    auto = [run for run in scheduled if not (run.summary or {}).get("approval_required")]
    durations = [
        (run.finished_at - run.created_at).total_seconds()
        for run in scheduled
        if run.finished_at and run.created_at
    ]
    decisions = [(run.summary or {}).get("approval_decision") for run in runs]
    decided = [d for d in decisions if d in ("approve", "reject")]
    return Metrics(
        total_runs=len(runs),
        finished_runs=len(finished),
        running=by_status.get("running", 0),
        awaiting_approval=by_status.get("awaiting_approval", 0),
        by_status=dict(by_status),
        auto_resolved_rate=round(len(auto) / len(finished), 4) if finished else None,
        avg_time_to_schedule_s=_mean(durations),
        approval_rate=round(decided.count("approve") / len(decided), 4) if decided else None,
        approvals_decided=len(decided),
        avg_tokens_per_run=_mean([float(run.input_tokens + run.output_tokens) for run in finished]),
    )
