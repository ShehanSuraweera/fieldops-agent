"""TOOL_TRANSPORT=mcp: the agent's tools, called through the CoolTech MCP server.

McpTools has exactly the EnterpriseTools interface and returns the same typed
ToolResult objects, so the graph, rules and step logging do not change.

The MCP client SDK is async and the graph is synchronous. McpConnection keeps one
long-lived MCP session inside a background event loop (an anyio blocking portal):
a single task owns the session for its whole life and serves calls from a queue,
so the session is opened and closed in the same task as anyio requires.
"""

import concurrent.futures
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, asynccontextmanager, suppress
from datetime import date, datetime
from typing import Any

import anyio
import httpx2
from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from pydantic import TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

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
    TechnicianAvailability,
    Ticket,
    TicketMessage,
    TicketUpdate,
    VendorOffer,
    WorkOrder,
)
from app.tools import AssetSummary, ToolError, ToolResult

CALL_TIMEOUT_S = 60.0  # the server's own REST calls retry within this

ClientFactory = Callable[[], Client]


def http_client_factory(url: str | None = None, api_key: str | None = None) -> ClientFactory:
    """Clients for the MCP server over Streamable HTTP, authenticating with X-API-Key."""
    url = url or settings.mcp_url
    headers = {"X-API-Key": api_key or settings.mcp_api_key}

    @asynccontextmanager
    async def transport() -> AsyncIterator[Any]:
        async with (
            httpx2.AsyncClient(
                headers=headers, timeout=httpx2.Timeout(CALL_TIMEOUT_S, read=CALL_TIMEOUT_S)
            ) as http,
            streamable_http_client(url, http_client=http) as streams,
        ):
            yield streams

    return lambda: Client(transport(), read_timeout_seconds=CALL_TIMEOUT_S, cache=None)


class McpUnavailable(Exception):
    pass


class McpConnection:
    """A thread-safe, synchronous facade over one MCP client session."""

    def __init__(self, client_factory: ClientFactory, call_timeout_s: float = CALL_TIMEOUT_S) -> None:
        self._client_factory = client_factory
        self._call_timeout_s = call_timeout_s
        self._lock = threading.Lock()
        self._portal_cm: AbstractContextManager[BlockingPortal] | None = None
        self._portal: BlockingPortal | None = None
        self._send: Any = None
        self._task: concurrent.futures.Future | None = None
        self._ready: concurrent.futures.Future | None = None

    def call(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        send = self._ensure_session()
        result: concurrent.futures.Future[CallToolResult] = concurrent.futures.Future()
        self._portal.call(send.send, (name, arguments, result))
        return result.result(timeout=self._call_timeout_s)

    def close(self) -> None:
        with self._lock:
            self._shutdown()

    def _ensure_session(self) -> Any:
        with self._lock:
            if self._task is not None and self._task.done():
                self._shutdown()  # the session died (e.g. server restarted): start a fresh one
            if self._task is None:
                self._portal_cm = start_blocking_portal()
                self._portal = self._portal_cm.__enter__()
                send, receive = self._portal.call(lambda: anyio.create_memory_object_stream(100))
                self._send = send
                self._ready = concurrent.futures.Future()
                self._task = self._portal.start_task_soon(self._serve, receive, self._ready)
            ready = self._ready
        try:
            ready.result(timeout=self._call_timeout_s)
        except Exception as exc:
            raise McpUnavailable(f"cannot connect to the MCP server: {exc}") from exc
        return self._send

    async def _serve(self, receive: Any, ready: concurrent.futures.Future) -> None:
        in_flight: set[concurrent.futures.Future] = set()

        async def handle(
            client: Client, name: str, arguments: dict, future: concurrent.futures.Future
        ) -> None:
            in_flight.add(future)
            try:
                future.set_result(await client.call_tool(name, arguments))
            except Exception as exc:
                future.set_exception(exc)
            finally:
                in_flight.discard(future)

        try:
            async with self._client_factory() as client, anyio.create_task_group() as tasks:
                ready.set_result(True)
                async with receive:
                    async for name, arguments, future in receive:
                        tasks.start_soon(handle, client, name, arguments, future)
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc if isinstance(exc, Exception) else McpUnavailable(repr(exc)))
            for future in list(in_flight):
                if not future.done():
                    future.set_exception(McpUnavailable(f"MCP session ended: {exc!r}"))
            raise

    def _shutdown(self) -> None:
        """Best effort: close the queue (ends the session task), wait for it, stop the event loop."""
        if self._send is not None and self._portal is not None:
            with suppress(Exception):
                self._portal.call(self._send.close)
        if self._task is not None:
            with suppress(Exception):
                self._task.result(timeout=5)
        if self._portal_cm is not None:
            with suppress(Exception):
                self._portal_cm.__exit__(None, None, None)
        self._portal_cm = self._portal = self._send = self._task = self._ready = None


class McpTools:
    """EnterpriseTools over MCP: same methods, same ToolResult types."""

    def __init__(self, connection: McpConnection, clock: Callable[[], datetime] = now_lk) -> None:
        self.connection = connection
        self.clock = clock

    def _call(self, name: str, schema: Any, **arguments: Any) -> ToolResult:
        args = to_jsonable_python({k: v for k, v in arguments.items() if v is not None})
        try:
            result = self.connection.call(name, args)
        except Exception as exc:
            return ToolResult.failure(
                ToolError(code="mcp_unavailable", message=f"{name}: {exc}", retryable=True)
            )
        if result.is_error:
            text = " ".join(getattr(part, "text", "") for part in result.content) or "MCP tool error"
            return ToolResult.failure(ToolError(code="mcp_tool_error", message=f"{name}: {text}"))
        payload = result.structured_content or {}
        if not payload.get("ok"):
            error = payload.get("error") or {"code": "invalid_response", "message": f"{name}: no result"}
            return ToolResult.failure(ToolError.model_validate(error))
        try:
            return ToolResult.success(TypeAdapter(schema).validate_python(payload.get("data")))
        except ValidationError as exc:
            return ToolResult.failure(
                ToolError(code="invalid_response", message=f"{name}: {exc.error_count()} schema errors")
            )

    # CRM

    def find_customer(self, email: str) -> ToolResult[Customer]:
        return self._call("find_customer", Customer, email=email)

    def create_ticket(
        self, customer_id: str, description: str, asset_id: str | None = None
    ) -> ToolResult[Ticket]:
        return self._call(
            "create_ticket", Ticket, customer_id=customer_id, description=description, asset_id=asset_id
        )

    def update_ticket(self, ticket_id: str, fields: TicketUpdate | dict[str, Any]) -> ToolResult[Ticket]:
        try:
            update = fields if isinstance(fields, TicketUpdate) else TicketUpdate.model_validate(fields)
        except ValidationError as exc:
            return ToolResult.failure(ToolError(code="invalid_input", message=str(exc)))
        payload = update.model_dump(mode="json", exclude_unset=True)
        return self._call("update_ticket", Ticket, ticket_id=ticket_id, fields=payload)

    def send_customer_message(self, ticket_id: str, body: str) -> ToolResult[TicketMessage]:
        return self._call("send_customer_message", TicketMessage, ticket_id=ticket_id, body=body)

    # FSM

    def find_asset(self, customer_id: str, query: str) -> ToolResult[list[Asset]]:
        # query may be "" (all of the customer's assets), so it is passed even when empty
        return self._call("find_asset", list[Asset], customer_id=customer_id, query=query)

    def get_asset_summary(self, asset_id: str, as_of: date | None = None) -> ToolResult[AssetSummary]:
        # Facts are computed on the run's business date, not the MCP server's clock.
        return self._call(
            "get_asset_summary", AssetSummary, asset_id=asset_id, as_of=as_of or self.clock().date()
        )

    def list_fault_codes(self, model: str) -> ToolResult[list[FaultCode]]:
        return self._call("list_fault_codes", list[FaultCode], model=model)

    def find_available_technicians(
        self, region: str, skill: str, earliest: datetime, latest: datetime
    ) -> ToolResult[list[TechnicianAvailability]]:
        return self._call(
            "find_available_technicians",
            list[TechnicianAvailability],
            region=region,
            skill=skill,
            earliest=earliest,
            latest=latest,
        )

    def create_work_order(
        self, ticket_id: str, asset_id: str, technician_id: str, scheduled_start: datetime, est_hours: float
    ) -> ToolResult[WorkOrder]:
        return self._call(
            "create_work_order",
            WorkOrder,
            ticket_id=ticket_id,
            asset_id=asset_id,
            technician_id=technician_id,
            scheduled_start=scheduled_start,
            est_hours=est_hours,
        )

    # ERP

    def check_part_stock(self, sku: str) -> ToolResult[Part]:
        return self._call("check_part_stock", Part, sku=sku)

    def reserve_part(self, sku: str, qty: int, ticket_id: str) -> ToolResult[Reservation]:
        return self._call("reserve_part", Reservation, sku=sku, qty=qty, ticket_id=ticket_id)

    def find_vendors(self, sku: str) -> ToolResult[list[VendorOffer]]:
        return self._call("find_vendors", list[VendorOffer], sku=sku)

    def create_purchase_order(
        self, vendor_id: str, lines: list[PartNeed], ticket_id: str, warranty_claim: bool
    ) -> ToolResult[PurchaseOrder]:
        return self._call(
            "create_purchase_order",
            PurchaseOrder,
            vendor_id=vendor_id,
            lines=lines,
            ticket_id=ticket_id,
            warranty_claim=warranty_claim,
        )

    def update_purchase_order(self, po_id: str, status: POStatus) -> ToolResult[PurchaseOrder]:
        return self._call("update_purchase_order", PurchaseOrder, po_id=po_id, status=status)
