"""MCP server exposing CoolTech's enterprise tools (the agent's own tool set) over MCP.

Any MCP client (the FieldOps agent with TOOL_TRANSPORT=mcp, Claude Desktop, the MCP
Inspector) gets the same 14 tools the agent uses, with the same `{ok, data, error}`
results. The tools are the agent's EnterpriseTools: one implementation, two transports.

Served over Streamable HTTP at /mcp (see services/mcp_server). Every request except
GET /health needs the X-API-Key header.
"""

import json
import secrets
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from app.config import settings
from app.enterprise import PartNeed
from app.tools import EnterpriseTools

INSTRUCTIONS = (
    "Tools for CoolTech Services' CRM, field service (FSM) and ERP systems: customers and tickets, "
    "assets and service history, fault catalog, technician calendars and work orders, parts stock, "
    "reservations, vendors and purchase orders. Every tool returns {ok, data, error}; check `ok`."
)

Json = dict[str, Any]


def build_server(tools: EnterpriseTools | Any) -> MCPServer:
    """Register every enterprise tool on a new MCP server. ``tools`` may be any object with the
    EnterpriseTools interface (tests pass an in-memory fake)."""
    server = MCPServer("cooltech-enterprise", instructions=INSTRUCTIONS, version="0.7.0")

    @server.tool()
    def find_customer(email: str) -> Json:
        """Identify a customer from the sender's email address."""
        return tools.find_customer(email=email).as_json()

    @server.tool()
    def create_ticket(customer_id: str, description: str, asset_id: str | None = None) -> Json:
        """Open a CRM support ticket for a customer."""
        return tools.create_ticket(
            customer_id=customer_id, description=description, asset_id=asset_id
        ).as_json()

    @server.tool()
    def update_ticket(ticket_id: str, fields: Json) -> Json:
        """Change a ticket's status, priority, sla_deadline, asset_id or resolution_note."""
        return tools.update_ticket(ticket_id=ticket_id, fields=fields).as_json()

    @server.tool()
    def send_customer_message(ticket_id: str, body: str) -> Json:
        """Log an outbound message to the customer on a ticket."""
        return tools.send_customer_message(ticket_id=ticket_id, body=body).as_json()

    @server.tool()
    def find_asset(customer_id: str, query: str) -> Json:
        """Find a customer's assets matching a description like 'Freezer #3' (may return several)."""
        return tools.find_asset(customer_id=customer_id, query=query).as_json()

    @server.tool()
    def get_asset_summary(asset_id: str, as_of: date | None = None) -> Json:
        """Asset details, recent service history and computed facts (under_warranty,
        repeat_failure_90d, age_years, last_technician_id, recent_faults) as of a date."""
        return tools.get_asset_summary(asset_id=asset_id, as_of=as_of).as_json()

    @server.tool()
    def list_fault_codes(model: str) -> Json:
        """The fault catalog for an asset model: codes, symptoms, likely parts, skill, hours."""
        return tools.list_fault_codes(model=model).as_json()

    @server.tool()
    def find_available_technicians(region: str, skill: str, earliest: datetime, latest: datetime) -> Json:
        """Open calendar slots of technicians with a skill in a region, between two times."""
        return tools.find_available_technicians(
            region=region, skill=skill, earliest=earliest, latest=latest
        ).as_json()

    @server.tool()
    def create_work_order(
        ticket_id: str, asset_id: str, technician_id: str, scheduled_start: datetime, est_hours: float
    ) -> Json:
        """Book a technician visit starting at an open slot."""
        return tools.create_work_order(
            ticket_id=ticket_id,
            asset_id=asset_id,
            technician_id=technician_id,
            scheduled_start=scheduled_start,
            est_hours=est_hours,
        ).as_json()

    @server.tool()
    def check_part_stock(sku: str) -> Json:
        """Stock level and compatible models for one part."""
        return tools.check_part_stock(sku=sku).as_json()

    @server.tool()
    def reserve_part(sku: str, qty: int, ticket_id: str) -> Json:
        """Hold in-stock parts for a ticket."""
        return tools.reserve_part(sku=sku, qty=qty, ticket_id=ticket_id).as_json()

    @server.tool()
    def find_vendors(sku: str) -> Json:
        """Every vendor's price, lead time and approval status for one part."""
        return tools.find_vendors(sku=sku).as_json()

    @server.tool()
    def create_purchase_order(
        vendor_id: str, lines: list[PartNeed], ticket_id: str, warranty_claim: bool
    ) -> Json:
        """Draft a purchase order; the ERP prices it from the vendor's price list."""
        return tools.create_purchase_order(
            vendor_id=vendor_id, lines=lines, ticket_id=ticket_id, warranty_claim=warranty_claim
        ).as_json()

    @server.tool()
    def update_purchase_order(
        po_id: str, status: Literal["draft", "pending_approval", "approved", "rejected", "sent"]
    ) -> Json:
        """Move a purchase order through draft -> pending_approval -> approved/rejected -> sent."""
        return tools.update_purchase_order(po_id=po_id, status=status).as_json()

    return server


# --- ASGI app ----------------------------------------------------------------------

ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


async def _send_json(send: Callable[[dict], Awaitable[None]], status: int, body: Json) -> None:
    payload = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


def with_api_key(app: ASGIApp, api_key: str) -> ASGIApp:
    """Require X-API-Key on every HTTP request; GET /health stays open for health checks."""

    async def guarded(scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            return await app(scope, receive, send)  # lifespan starts the MCP session manager
        if scope["path"] == "/health":
            return await _send_json(send, 200, {"status": "ok"})
        headers = dict(scope.get("headers") or [])
        supplied = headers.get(b"x-api-key", b"").decode()
        if not secrets.compare_digest(supplied, api_key):
            error = {
                "code": "unauthorized",
                "message": "Missing or invalid X-API-Key header",
                "details": None,
            }
            return await _send_json(send, 401, {"error": error})
        return await app(scope, receive, send)

    return guarded


def create_app() -> ASGIApp:
    server = build_server(EnterpriseTools())
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["localhost:*", "127.0.0.1:*", "mcp_server:*"],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
    )
    return with_api_key(server.streamable_http_app(transport_security=security), settings.mcp_api_key)
