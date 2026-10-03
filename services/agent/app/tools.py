"""Enterprise tools: thin typed wrappers over the mock CRM, FSM and ERP APIs.

Every tool returns ``ToolResult {ok, data, error}`` and never raises for API or
network failures, so the graph can log the outcome and decide what to do.

Retry policy (10 s timeout, up to 2 retries with exponential backoff):

- GET and PATCH are idempotent, so they retry on timeouts, connection errors,
  5xx and 429.
- POST retries only when the request provably never reached the server (could
  not connect). A POST that timed out may have succeeded, and retrying it could
  reserve stock twice or create a duplicate purchase order.
- 4xx responses are never retried.
"""

import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.config import now_lk, settings
from app.enterprise import (
    Asset,
    Customer,
    FaultCode,
    Part,
    PartNeed,
    POStatus,
    PurchaseOrder,
    Reservation,
    ServiceRecord,
    TechnicianAvailability,
    Ticket,
    TicketMessage,
    TicketUpdate,
    VendorOffer,
    WorkOrder,
)
from app.rules import AssetFacts, RulesConfig, compute_asset_facts, load_rules

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_S = 0.5
HISTORY_IN_SUMMARY = 5


class ToolError(BaseModel):
    code: str
    message: str
    status_code: int | None = None
    retryable: bool = False
    attempts: int = 1


class ToolResult[T](BaseModel):
    ok: bool
    data: T | None = None
    error: ToolError | None = None

    @classmethod
    def success(cls, data: T) -> "ToolResult[T]":
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error: ToolError) -> "ToolResult[T]":
        return cls(ok=False, error=error)

    def compact(self) -> dict[str, Any]:
        """JSON-ready dict for logs and LLM context (None fields dropped)."""
        return self.model_dump(mode="json", exclude_none=True)

    def as_json(self) -> dict[str, Any]:
        """Complete JSON-ready dict, None fields included, for other services (the MCP server)."""
        return self.model_dump(mode="json")


class AssetSummary(BaseModel):
    asset: Asset
    facts: AssetFacts
    recent_history: list[ServiceRecord]


# --- HTTP client ---------------------------------------------------------------

# Timeouts and connection failures where the request never left this process.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class EnterpriseClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_s: float = DEFAULT_BACKOFF_S,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url or settings.mock_base_url,
            headers={"X-API-Key": api_key or settings.mock_api_key},
            timeout=timeout_s,
            transport=transport,
        )

    @property
    def timeout(self) -> httpx.Timeout:
        return self._http.timeout

    def close(self) -> None:
        self._http.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> tuple[Any, ToolError | None]:
        """Send one logical request with retries. Returns (json body, None) or (None, error)."""
        clean_params = {k: _param(v) for k, v in (params or {}).items() if v is not None}
        attempt = 0
        while True:
            attempt += 1
            outcome = self._attempt(method, path, clean_params, json)
            if not isinstance(outcome, ToolError):
                return outcome, None
            outcome.attempts = attempt
            if not outcome.retryable or attempt > self.max_retries:
                return None, outcome
            self._sleep(self.backoff_s * 2 ** (attempt - 1))

    def _attempt(self, method: str, path: str, params: dict[str, Any], json: Any) -> Any:
        """One HTTP round trip. Returns the parsed JSON body, or a ToolError."""
        idempotent = method.upper() != "POST"
        try:
            response = self._http.request(method, path, params=params, json=json)
        except httpx.TimeoutException as exc:
            return ToolError(
                code="timeout",
                message=f"{method} {path} timed out: {exc.__class__.__name__}",
                retryable=idempotent or isinstance(exc, _NOT_SENT),
            )
        except httpx.TransportError as exc:
            return ToolError(
                code="connection_error",
                message=f"{method} {path} failed: {exc.__class__.__name__}: {exc}",
                retryable=idempotent or isinstance(exc, _NOT_SENT),
            )

        if response.is_success:
            try:
                return response.json()
            except ValueError:
                return ToolError(
                    code="invalid_response",
                    message=f"{method} {path} returned non-JSON",
                    status_code=response.status_code,
                )
        transient = response.status_code >= 500 or response.status_code == 429
        return _error_from_response(response, retryable=transient and idempotent)


def _param(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _error_from_response(response: httpx.Response, *, retryable: bool) -> ToolError:
    """Use the mock's {"error": {"code", "message"}} body when present."""
    code, message = "http_error", f"HTTP {response.status_code}"
    try:
        body = response.json()["error"]
        code, message = body["code"], body["message"]
    except (ValueError, KeyError, TypeError):
        pass
    return ToolError(code=code, message=message, status_code=response.status_code, retryable=retryable)


# --- tools ---------------------------------------------------------------------


class EnterpriseTools:
    """The agent's tool set. One instance per run (or shared; it holds no run state)."""

    def __init__(
        self,
        client: EnterpriseClient | None = None,
        rules: RulesConfig | None = None,
        clock: Callable[[], datetime] = now_lk,
    ) -> None:
        self.client = client or EnterpriseClient()
        self.rules = rules or load_rules()
        self.clock = clock

    def _call[T](
        self, schema: type[T] | Any, method: str, path: str, *, params: dict | None = None, json: Any = None
    ) -> ToolResult[T]:
        body, error = self.client.request(method, path, params=params, json=json)
        if error:
            return ToolResult.failure(error)
        try:
            return ToolResult.success(TypeAdapter(schema).validate_python(body))
        except ValidationError as exc:
            return ToolResult.failure(
                ToolError(
                    code="invalid_response", message=f"{method} {path}: {exc.error_count()} schema errors"
                )
            )

    # CRM

    def find_customer(self, email: str) -> ToolResult[Customer]:
        """Identify the customer from the ticket sender's email."""
        result = self._call(list[Customer], "GET", "/crm/customers", params={"email": email})
        if not result.ok:
            return ToolResult.failure(result.error)
        if not result.data:
            return ToolResult.failure(
                ToolError(code="not_found", message=f"No customer with email '{email}'", status_code=404)
            )
        return ToolResult.success(result.data[0])

    def create_ticket(
        self, customer_id: str, description: str, asset_id: str | None = None
    ) -> ToolResult[Ticket]:
        payload = {"customer_id": customer_id, "description": description, "asset_id": asset_id}
        return self._call(Ticket, "POST", "/crm/tickets", json=payload)

    def update_ticket(self, ticket_id: str, fields: TicketUpdate | dict[str, Any]) -> ToolResult[Ticket]:
        """Change status, priority, SLA deadline, asset or notes."""
        try:
            update = fields if isinstance(fields, TicketUpdate) else TicketUpdate.model_validate(fields)
        except ValidationError as exc:
            return ToolResult.failure(ToolError(code="invalid_input", message=str(exc)))
        payload = update.model_dump(mode="json", exclude_unset=True)
        return self._call(Ticket, "PATCH", f"/crm/tickets/{ticket_id}", json=payload)

    def send_customer_message(self, ticket_id: str, body: str) -> ToolResult[TicketMessage]:
        """Log the outbound reply (stands in for email or SMS)."""
        payload = {"body": body, "direction": "outbound"}
        return self._call(TicketMessage, "POST", f"/crm/tickets/{ticket_id}/messages", json=payload)

    # FSM

    def find_asset(self, customer_id: str, query: str) -> ToolResult[list[Asset]]:
        """Match a description like "Freezer #3" to assets. May return several candidates."""
        return self._call(list[Asset], "GET", "/fsm/assets", params={"customer_id": customer_id, "q": query})

    def get_asset_summary(self, asset_id: str, as_of: date | None = None) -> ToolResult[AssetSummary]:
        """Asset, recent history and rule-computed facts (warranty, repeat failure, age...).

        Facts are computed as of ``as_of`` (default: this tool set's clock), so a remote
        caller can ask for the facts on its own business date.
        """
        asset = self._call(Asset, "GET", f"/fsm/assets/{asset_id}")
        if not asset.ok:
            return ToolResult.failure(asset.error)
        history = self._call(list[ServiceRecord], "GET", f"/fsm/assets/{asset_id}/history")
        if not history.ok:
            return ToolResult.failure(history.error)
        facts = compute_asset_facts(asset.data, history.data, as_of or self.clock().date(), self.rules)
        return ToolResult.success(
            AssetSummary(asset=asset.data, facts=facts, recent_history=history.data[:HISTORY_IN_SUMMARY])
        )

    def list_fault_codes(self, model: str) -> ToolResult[list[FaultCode]]:
        """The fault catalog the diagnosis must choose from."""
        return self._call(list[FaultCode], "GET", "/fsm/fault-codes", params={"model": model})

    def find_available_technicians(
        self, region: str, skill: str, earliest: datetime, latest: datetime
    ) -> ToolResult[list[TechnicianAvailability]]:
        params = {"region": region, "skill": skill, "from": earliest, "to": latest}
        return self._call(list[TechnicianAvailability], "GET", "/fsm/technicians/available", params=params)

    def create_work_order(
        self, ticket_id: str, asset_id: str, technician_id: str, scheduled_start: datetime, est_hours: float
    ) -> ToolResult[WorkOrder]:
        """Book the technician for the visit."""
        payload = {
            "ticket_id": ticket_id,
            "asset_id": asset_id,
            "technician_id": technician_id,
            "scheduled_start": scheduled_start.isoformat(),
            "est_hours": est_hours,
        }
        return self._call(WorkOrder, "POST", "/fsm/work-orders", json=payload)

    # ERP

    def check_part_stock(self, sku: str) -> ToolResult[Part]:
        return self._call(Part, "GET", f"/erp/parts/{sku}")

    def reserve_part(self, sku: str, qty: int, ticket_id: str) -> ToolResult[Reservation]:
        """Hold in-stock parts for the job."""
        payload = {"sku": sku, "qty": qty, "ticket_id": ticket_id}
        return self._call(Reservation, "POST", "/erp/reservations", json=payload)

    def find_vendors(self, sku: str) -> ToolResult[list[VendorOffer]]:
        """Every vendor's price, lead time and approval status for one sku."""
        result = self._call(list[dict], "GET", "/erp/vendors", params={"sku": sku})
        if not result.ok:
            return ToolResult.failure(result.error)
        offers = [
            VendorOffer(
                vendor_id=v["id"],
                vendor_name=v["name"],
                sku=sku,
                approved=v["approved"],
                lead_time_days=v["lead_time_days"],
                price_lkr=v["price_lkr"],
            )
            for v in result.data
        ]
        return ToolResult.success(offers)

    def create_purchase_order(
        self, vendor_id: str, lines: list[PartNeed], ticket_id: str, warranty_claim: bool
    ) -> ToolResult[PurchaseOrder]:
        """Draft a PO. The ERP prices it from the vendor's price list."""
        payload = {
            "vendor_id": vendor_id,
            "ticket_id": ticket_id,
            "lines": [line.model_dump() for line in lines],
            "warranty_claim": warranty_claim,
            "created_by": "agent",
        }
        return self._call(PurchaseOrder, "POST", "/erp/purchase-orders", json=payload)

    def update_purchase_order(self, po_id: str, status: POStatus) -> ToolResult[PurchaseOrder]:
        """Move a PO along draft -> pending_approval -> approved/rejected -> sent."""
        return self._call(PurchaseOrder, "PATCH", f"/erp/purchase-orders/{po_id}", json={"status": status})
