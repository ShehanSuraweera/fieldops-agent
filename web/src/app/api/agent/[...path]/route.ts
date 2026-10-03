// Server-side proxy to the agent API. It adds the API key, so the browser never sees it,
// forwards only the endpoints the dashboard uses, and streams Server-Sent Events through as-is.
import type { NextRequest } from "next/server";

const AGENT_URL = process.env.AGENT_URL ?? "http://localhost:8002";
const AGENT_API_KEY = process.env.AGENT_API_KEY ?? "dev-agent-key";

const ALLOWED: { method: string; pattern: RegExp }[] = [
  { method: "GET", pattern: /^runs$/ },
  { method: "POST", pattern: /^runs$/ },
  { method: "GET", pattern: /^runs\/[\w-]+$/ },
  { method: "GET", pattern: /^runs\/[\w-]+\/events$/ },
  { method: "POST", pattern: /^runs\/[\w-]+\/approval$/ },
  { method: "GET", pattern: /^approvals\/pending$/ },
  { method: "GET", pattern: /^metrics$/ },
];

function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message, details: null } }, { status });
}

async function forward(request: NextRequest, ctx: RouteContext<"/api/agent/[...path]">): Promise<Response> {
  const { path } = await ctx.params;
  const subpath = path.join("/");
  if (!ALLOWED.some((rule) => rule.method === request.method && rule.pattern.test(subpath))) {
    return errorResponse(404, "not_found", `No such agent endpoint: ${request.method} /${subpath}`);
  }

  const headers = new Headers({ "X-API-Key": AGENT_API_KEY });
  for (const name of ["content-type", "last-event-id", "accept"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${AGENT_URL}/${subpath}${request.nextUrl.search}`, {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      signal: request.signal, // closing the browser tab also closes the upstream event stream
    });
  } catch {
    return errorResponse(502, "agent_unreachable", `Cannot reach the agent API at ${AGENT_URL}`);
  }

  const responseHeaders = new Headers({ "Cache-Control": "no-store" });
  const contentType = upstream.headers.get("content-type");
  if (contentType) responseHeaders.set("Content-Type", contentType);
  if (contentType?.startsWith("text/event-stream")) {
    responseHeaders.set("Cache-Control", "no-cache, no-transform");
    responseHeaders.set("X-Accel-Buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const GET = forward;
export const POST = forward;
