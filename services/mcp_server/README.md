# CoolTech MCP server

Exposes CoolTech's enterprise tools over the [Model Context Protocol](https://modelcontextprotocol.io).
These are the same 14 tools the FieldOps agent uses, with the same `{ok, data, error}` results. The code is
the agent's own implementation (`services/agent/app/mcp_server.py`, wrapping `app/tools.py`), packaged
here as a slim image, so REST and MCP can never drift apart.

- Endpoint: `http://localhost:8003/mcp` (Streamable HTTP)
- Auth: `X-API-Key: dev-mcp-key` (set by `MCP_API_KEY`); `GET /health` is open
- Tools:
  - **CRM:** `find_customer`, `create_ticket`, `update_ticket`, `send_customer_message`
  - **FSM:** `find_asset`, `get_asset_summary`, `list_fault_codes`, `find_available_technicians`,
    `create_work_order`
  - **ERP:** `check_part_stock`, `reserve_part`, `find_vendors`, `create_purchase_order`,
    `update_purchase_order`

The agent uses it when `TOOL_TRANSPORT=mcp`. To try it with any MCP client, for example the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP, URL: http://localhost:8003/mcp, header X-API-Key: dev-mcp-key
```

The tools change real (mock) data: reserving parts, booking technicians, drafting POs. Reseed with
`POST /admin/reseed` on the mock, or "Reset demo data" in the dashboard.
