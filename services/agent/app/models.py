"""agent.runs and agent.run_steps: the observability log the dashboard and evals read."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = {"schema": "agent"}

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ticket_id: Mapped[str | None] = mapped_column(String(20), index=True)
    customer_email: Mapped[str] = mapped_column(String(200))
    raw_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    clock: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # the agent's "now" for this run
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    final_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list["RunStep"]] = relationship(order_by="RunStep.seq")


class RunStep(Base):
    """One node, tool call or LLM call within a run."""

    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_steps_seq"), {"schema": "agent"})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent.runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(10))  # node | tool | llm
    tool_name: Mapped[str | None] = mapped_column(String(60))  # tool name, or prompt name for llm rows
    input: Mapped[Any] = mapped_column(JSONB, nullable=True)
    output: Mapped[Any] = mapped_column(JSONB, nullable=True)
    ok: Mapped[bool] = mapped_column(default=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
