// Typed client for the agent API. The browser only talks to /api/agent/*, a server-side
// proxy that adds the API key, so no secret ever reaches the client.

export type RunStatus =
  | "running"
  | "awaiting_approval"
  | "scheduled"
  | "needs_info"
  | "needs_manual_procurement"
  | "needs_human";

export type Step = {
  run_id: string;
  seq: number;
  node: string;
  kind: "node" | "tool" | "llm";
  tool_name: string | null;
  input: unknown;
  output: unknown;
  ok: boolean;
  latency_ms: number;
  input_tokens: number | null;
  output_tokens: number | null;
  error: string | null;
};

export type RunSummary = {
  status: RunStatus;
  ticket_id: string | null;
  customer_id: string | null;
  asset_id: string | null;
  fault_code: string | null;
  confidence: number | null;
  inspection: boolean | null;
  priority: string | null;
  sla_deadline: string | null;
  parts: { sku: string; status: string }[];
  po_created: boolean;
  po_id: string | null;
  po_status: string | null;
  po_total_lkr: number | null;
  warranty_claim: boolean | null;
  approval_required: boolean;
  approval_decision: string | null;
  work_order_id: string | null;
  technician_id: string | null;
  scheduled_start: string | null;
  sla_risk: boolean;
  customer_message: string | null;
  errors: string[];
};

export type PurchaseOrder = {
  id: string;
  vendor_id: string;
  vendor_name: string;
  status: string;
  total_lkr: number;
  warranty_claim: boolean;
  lead_time_days: number;
  parts_ready_at: string | null;
  sla_risk: boolean;
  reason: string;
  lines: { sku: string; qty: number; unit_price_lkr: number }[];
};

export type Diagnosis = {
  fault_code: string;
  fault_name: string;
  confidence: number;
  reasoning: string;
  inspection: boolean;
  skill: string;
  est_hours: number;
};

/** The subset of the agent's final state the dashboard reads. */
export type FinalState = {
  customer?: { id: string; name: string; contract_tier: string; region: string } | null;
  asset?: { id: string; name: string; model: string; site: string; region: string } | null;
  asset_facts?: { under_warranty: boolean; repeat_failure_90d: boolean } | null;
  diagnosis?: Diagnosis | null;
  ticket_notes?: string[];
  purchase_order?: PurchaseOrder | null;
  approval?: { required: boolean; decision: string | null; comment: string | null } | null;
  work_order?: {
    id: string;
    technician_id: string;
    technician_name: string;
    scheduled_start: string;
    scheduled_end: string;
    est_hours: number;
    sla_risk: boolean;
    reason: string;
  } | null;
};

export type Run = {
  id: string;
  ticket_id: string | null;
  customer_email: string;
  raw_text: string;
  status: RunStatus;
  clock: string;
  summary: RunSummary | null;
  final_state: FinalState | null;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  created_at: string | null;
  finished_at: string | null;
};

export type RunDetail = Run & { steps: Step[] };

export type PendingApproval = {
  run_id: string;
  ticket_id: string | null;
  requested_at: string | null;
  priority: string | null;
  sla_deadline: string | null;
  customer: { id: string; name: string; contract_tier: string };
  asset: { id: string; name: string; model: string; site: string };
  diagnosis: { fault_code: string; fault_name: string; confidence: number; reasoning: string };
  purchase_order: PurchaseOrder;
};

export type Metrics = {
  total_runs: number;
  finished_runs: number;
  running: number;
  awaiting_approval: number;
  by_status: Record<string, number>;
  auto_resolved_rate: number | null;
  avg_time_to_schedule_s: number | null;
  approval_rate: number | null;
  approvals_decided: number;
  avg_tokens_per_run: number | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/agent/${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let code: string | undefined;
    try {
      const body = await response.json();
      message = body?.error?.message ?? message;
      code = body?.error?.code;
    } catch {
      // not JSON; keep the generic message
    }
    throw new ApiError(message, response.status, code);
  }
  return response.json() as Promise<T>;
}

export const api = {
  startRun: (body: { customer_email: string; text: string; now?: string }) =>
    request<{ run_id: string; status: string }>("runs", { method: "POST", body: JSON.stringify(body) }),
  run: (id: string) => request<RunDetail>(`runs/${encodeURIComponent(id)}`),
  runs: (limit = 100) => request<Run[]>(`runs?limit=${limit}`),
  metrics: () => request<Metrics>("metrics"),
  pendingApprovals: () => request<PendingApproval[]>("approvals/pending"),
  decide: (id: string, decision: "approve" | "reject", comment: string, now?: string) =>
    request<{ run_id: string; status: string }>(`runs/${encodeURIComponent(id)}/approval`, {
      method: "POST",
      body: JSON.stringify({ decision, comment: comment || null, ...(now ? { now } : {}) }),
    }),
};

export function eventsUrl(runId: string, afterSeq: number): string {
  return `/api/agent/runs/${encodeURIComponent(runId)}/events?after=${afterSeq}`;
}
