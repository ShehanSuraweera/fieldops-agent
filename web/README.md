# FieldOps dashboard

Next.js (App Router) dashboard for the FieldOps agent: new tickets, live run timelines, the approvals inbox
and run history with KPIs. The browser talks only to `/api/agent/*` and `/api/demo/reset`. Those route
handlers add the API keys on the server, so no key reaches the client.

Run it with the rest of the stack from the repo root (`docker compose up -d --build`), then open
<http://localhost:3000>. For local development against a running stack:

```bash
npm install
AGENT_URL=http://localhost:8002 MOCK_URL=http://localhost:8001 npm run dev
```

Checks: `npm run lint`, `npm run typecheck` and `npm run build`.
