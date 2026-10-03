// "Reset demo data": reseeds the mock CRM/FSM/ERP for one date (POST /admin/reseed on the mock).
const MOCK_URL = process.env.MOCK_URL ?? "http://localhost:8001";
const MOCK_API_KEY = process.env.MOCK_API_KEY ?? "dev-mock-key";

export async function POST(request: Request): Promise<Response> {
  const body = (await request.json().catch(() => ({}))) as { today?: unknown };
  const today = typeof body.today === "string" ? body.today : "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(today)) {
    return Response.json(
      { error: { code: "validation_error", message: "today must be YYYY-MM-DD", details: null } },
      { status: 422 },
    );
  }
  try {
    const upstream = await fetch(`${MOCK_URL}/admin/reseed`, {
      method: "POST",
      headers: { "X-API-Key": MOCK_API_KEY, "Content-Type": "application/json" },
      body: JSON.stringify({ today }),
      cache: "no-store",
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json(
      { error: { code: "mock_unreachable", message: `Cannot reach the mock systems at ${MOCK_URL}`, details: null } },
      { status: 502 },
    );
  }
}
