"""Persistence for runs and steps: Postgres in the service, in-memory in tests."""

import threading
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Run, RunStep


class StepRecord(BaseModel):
    run_id: str
    seq: int
    node: str
    kind: str
    tool_name: str | None = None
    input: Any = None
    output: Any = None
    ok: bool = True
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


class RunRecord(BaseModel):
    id: str
    ticket_id: str | None
    customer_email: str
    raw_text: str
    status: str
    clock: datetime
    summary: dict[str, Any] | None = None
    final_state: dict[str, Any] | None = None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: datetime | None = None
    finished_at: datetime | None = None


class RunStore(Protocol):
    def create_run(self, run_id: str, customer_email: str, raw_text: str, clock: datetime) -> None: ...
    def add_step(self, step: StepRecord) -> None: ...
    def update_run(self, run_id: str, **fields: Any) -> None: ...
    def get_run(self, run_id: str) -> RunRecord | None: ...
    def list_runs(self, limit: int = 50, status: str | None = None) -> list[RunRecord]: ...
    def list_steps(self, run_id: str, after_seq: int = 0) -> list[StepRecord]: ...
    def last_seq(self, run_id: str) -> int: ...
    def claim_paused(self, run_id: str) -> bool:
        """Atomically move a run from awaiting_approval to running. False if it was not paused."""
        ...


class MemoryRunStore:
    def __init__(self) -> None:
        self.runs: dict[str, RunRecord] = {}
        self.steps: list[StepRecord] = []
        self._lock = threading.Lock()

    def create_run(self, run_id: str, customer_email: str, raw_text: str, clock: datetime) -> None:
        self.runs[run_id] = RunRecord(
            id=run_id, ticket_id=None, customer_email=customer_email, raw_text=raw_text, status="running",
            clock=clock, created_at=clock,
        )  # fmt: skip

    def add_step(self, step: StepRecord) -> None:
        self.steps.append(step)

    def update_run(self, run_id: str, **fields: Any) -> None:
        self.runs[run_id] = self.runs[run_id].model_copy(update=fields)

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.runs.get(run_id)

    def list_runs(self, limit: int = 50, status: str | None = None) -> list[RunRecord]:
        runs = [r for r in reversed(self.runs.values()) if status is None or r.status == status]
        return runs[:limit]

    def list_steps(self, run_id: str, after_seq: int = 0) -> list[StepRecord]:
        return [s for s in self.steps if s.run_id == run_id and s.seq > after_seq]

    def last_seq(self, run_id: str) -> int:
        return max((s.seq for s in self.steps if s.run_id == run_id), default=0)

    def claim_paused(self, run_id: str) -> bool:
        with self._lock:
            run = self.runs.get(run_id)
            if run is None or run.status != "awaiting_approval":
                return False
            self.runs[run_id] = run.model_copy(update={"status": "running"})
            return True


class SqlRunStore:
    def __init__(self, database_url: str | None = None) -> None:
        self.engine = create_engine(database_url or settings.database_url, pool_pre_ping=True)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def _session(self) -> Session:
        return self._sessions()

    def create_run(self, run_id: str, customer_email: str, raw_text: str, clock: datetime) -> None:
        with self._session() as session:
            session.add(
                Run(
                    id=run_id, customer_email=customer_email, raw_text=raw_text, status="running", clock=clock
                )
            )
            session.commit()

    def add_step(self, step: StepRecord) -> None:
        with self._session() as session:
            session.add(RunStep(**step.model_dump()))
            session.commit()

    def update_run(self, run_id: str, **fields: Any) -> None:
        with self._session() as session:
            run = session.get(Run, run_id)
            for key, value in fields.items():
                setattr(run, key, value)
            session.commit()

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._session() as session:
            run = session.get(Run, run_id)
            return _run_record(run) if run else None

    def list_runs(self, limit: int = 50, status: str | None = None) -> list[RunRecord]:
        stmt = select(Run).order_by(Run.created_at.desc(), Run.id).limit(limit)
        if status:
            stmt = stmt.where(Run.status == status)
        with self._session() as session:
            return [_run_record(r) for r in session.scalars(stmt)]

    def list_steps(self, run_id: str, after_seq: int = 0) -> list[StepRecord]:
        stmt = select(RunStep).where(RunStep.run_id == run_id, RunStep.seq > after_seq).order_by(RunStep.seq)
        with self._session() as session:
            return [_step_record(s) for s in session.scalars(stmt)]

    def last_seq(self, run_id: str) -> int:
        with self._session() as session:
            return session.scalar(select(func.max(RunStep.seq)).where(RunStep.run_id == run_id)) or 0

    def claim_paused(self, run_id: str) -> bool:
        with self._session() as session:
            claimed = session.execute(
                update(Run)
                .where(Run.id == run_id, Run.status == "awaiting_approval")
                .values(status="running")
                .returning(Run.id)
            ).first()
            session.commit()
            return claimed is not None

    def count_runs(self) -> int:
        with self._session() as session:
            return session.scalar(select(func.count()).select_from(Run)) or 0


def _run_record(run: Run) -> RunRecord:
    return RunRecord.model_validate(run, from_attributes=True)


def _step_record(step: RunStep) -> StepRecord:
    return StepRecord.model_validate(step, from_attributes=True)
