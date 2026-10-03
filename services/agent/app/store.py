"""Persistence for runs and steps: Postgres in the service, in-memory in tests."""

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
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
    def list_runs(self, limit: int = 50) -> list[RunRecord]: ...
    def list_steps(self, run_id: str) -> list[StepRecord]: ...


class MemoryRunStore:
    def __init__(self) -> None:
        self.runs: dict[str, RunRecord] = {}
        self.steps: list[StepRecord] = []

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

    def list_runs(self, limit: int = 50) -> list[RunRecord]:
        return list(reversed(self.runs.values()))[:limit]

    def list_steps(self, run_id: str) -> list[StepRecord]:
        return [s for s in self.steps if s.run_id == run_id]


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

    def list_runs(self, limit: int = 50) -> list[RunRecord]:
        with self._session() as session:
            runs = session.scalars(select(Run).order_by(Run.created_at.desc(), Run.id).limit(limit))
            return [_run_record(r) for r in runs]

    def list_steps(self, run_id: str) -> list[StepRecord]:
        with self._session() as session:
            steps = session.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq))
            return [_step_record(s) for s in steps]

    def count_runs(self) -> int:
        with self._session() as session:
            return session.scalar(select(func.count()).select_from(Run)) or 0


def _run_record(run: Run) -> RunRecord:
    return RunRecord.model_validate(run, from_attributes=True)


def _step_record(step: RunStep) -> StepRecord:
    return StepRecord.model_validate(step, from_attributes=True)
