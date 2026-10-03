"""Graph state and the schemas every LLM output is validated against."""

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

RunStatus = Literal["running", "needs_info", "awaiting_approval", "scheduled", "needs_human"]


class TicketState(TypedDict, total=False):
    run_id: str
    now: str  # ISO timestamp of the run's clock; every date rule uses this
    ticket_id: str | None
    raw_text: str
    customer_email: str
    intake: dict[str, Any] | None  # IntakeExtraction
    customer: dict[str, Any] | None
    asset: dict[str, Any] | None
    asset_facts: dict[str, Any] | None
    history: list[dict[str, Any]]
    diagnosis: dict[str, Any] | None  # fault_code, confidence, parts, skill, reasoning, inspection
    priority: str | None  # P1, P2, P3
    sla_deadline: str | None
    ticket_notes: list[str]
    parts_plan: list[dict[str, Any]]  # per part: in_stock | reserved | needs_po
    parts_ready_at: str | None
    purchase_order: dict[str, Any] | None
    approval: dict[str, Any] | None  # required, decision, comment
    work_order: dict[str, Any] | None
    customer_message: str | None
    summary: dict[str, Any] | None
    status: RunStatus
    errors: list[str]


def initial_state(run_id: str, now: str, customer_email: str, raw_text: str) -> TicketState:
    return TicketState(
        run_id=run_id,
        now=now,
        ticket_id=None,
        raw_text=raw_text,
        customer_email=customer_email,
        intake=None,
        customer=None,
        asset=None,
        asset_facts=None,
        history=[],
        diagnosis=None,
        priority=None,
        sla_deadline=None,
        ticket_notes=[],
        parts_plan=[],
        parts_ready_at=None,
        purchase_order=None,
        approval=None,
        work_order=None,
        customer_message=None,
        summary=None,
        status="running",
        errors=[],
    )


# --- LLM outputs -----------------------------------------------------------------


class IntakeExtraction(BaseModel):
    """Structured fields the LLM reads out of the complaint. Facts only, no decisions."""

    asset_description: str | None = Field(
        description="The asset as the customer names it, copied verbatim (e.g. 'Freezer #3'); "
        "null if not named"
    )
    site_hint: str | None = Field(description="Branch or site mentioned (e.g. 'Colombo 7'); null if none")
    symptoms: list[str] = Field(description="Observable symptoms in short phrases")
    asset_down: bool = Field(description="True if the unit has stopped working or is not cooling at all")
    urgency_cues: list[str] = Field(description="Phrases signalling urgency (e.g. 'stock at risk')")


class ClarifyingQuestion(BaseModel):
    question: str = Field(min_length=10, max_length=600)


class Diagnosis(BaseModel):
    """The LLM's choice of fault code. Validated against the catalog it was shown."""

    fault_code: str
    confidence: float = Field(ge=0, le=1)
    reasoning: str = Field(min_length=5, max_length=1000)

    @field_validator("fault_code")
    @classmethod
    def _from_catalog(cls, code: str, info: ValidationInfo) -> str:
        allowed = (info.context or {}).get("allowed_codes")
        if allowed is not None and code not in allowed:
            raise ValueError(f"fault_code must be one of {sorted(allowed)}")
        return code


class CustomerMessage(BaseModel):
    body: str = Field(min_length=20, max_length=1200)

    @model_validator(mode="after")
    def _keeps_the_facts(self, info: ValidationInfo) -> "CustomerMessage":
        for fact in (info.context or {}).get("must_include", []):
            if fact not in self.body:
                raise ValueError(f"body must include {fact!r} exactly as given")
        return self
