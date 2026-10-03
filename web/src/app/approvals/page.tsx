"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, type PendingApproval } from "@/lib/api";
import { formatDateTime, formatLkr, formatPercent } from "@/lib/format";
import { ApprovalPanel } from "@/components/ApprovalPanel";
import { Card, ErrorNote, Field } from "@/components/ui";

function ApprovalCard({ item }: { item: PendingApproval }) {
  const po = item.purchase_order;
  return (
    <Card
      title={
        <>
          {po.id}: {formatLkr(po.total_lkr)} from {po.vendor_name}
        </>
      }
      actions={
        <Link href={`/runs/${item.run_id}`} className="text-sm text-sky-700 hover:underline">
          View run →
        </Link>
      }
    >
      <dl className="grid gap-4 md:grid-cols-3">
        <Field label="Customer">
          {item.customer.name} ({item.customer.contract_tier})
        </Field>
        <Field label="Asset">
          {item.asset.name} at {item.asset.site} · {item.asset.model}
        </Field>
        <Field label="Ticket / priority / SLA">
          {item.ticket_id} · {item.priority} · {formatDateTime(item.sla_deadline)}
        </Field>
      </dl>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="text-xs uppercase tracking-wide text-slate-500">Why these parts</h3>
          <p className="mt-1 text-sm">
            <span className="font-medium">{item.diagnosis.fault_name}</span>{" "}
            <span className="font-mono text-xs">({item.diagnosis.fault_code})</span>, confidence{" "}
            {formatPercent(item.diagnosis.confidence)}
          </p>
          <p className="mt-1 text-sm text-slate-600">{item.diagnosis.reasoning}</p>
        </div>
        <div>
          <h3 className="text-xs uppercase tracking-wide text-slate-500">Why this vendor</h3>
          <p className="mt-1 text-sm text-slate-700">{po.reason}</p>
          <p className="mt-1 text-sm text-slate-600">
            Lead time {po.lead_time_days} day(s) · parts ready {formatDateTime(po.parts_ready_at)}
            {po.sla_risk && <span className="ml-2 rounded bg-rose-50 px-1.5 text-xs text-rose-700">SLA at risk</span>}
            {po.warranty_claim && (
              <span className="ml-2 rounded bg-emerald-50 px-1.5 text-xs text-emerald-700">warranty claim</span>
            )}
          </p>
        </div>
      </div>

      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
            <th className="py-1">Part</th>
            <th className="py-1 text-right">Qty</th>
            <th className="py-1 text-right">Unit price</th>
            <th className="py-1 text-right">Line total</th>
          </tr>
        </thead>
        <tbody>
          {po.lines.map((line) => (
            <tr key={line.sku} className="border-t border-slate-100">
              <td className="py-1 font-mono text-xs">{line.sku}</td>
              <td className="py-1 text-right">{line.qty}</td>
              <td className="py-1 text-right">{formatLkr(line.unit_price_lkr)}</td>
              <td className="py-1 text-right">{formatLkr(line.unit_price_lkr * line.qty)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="mt-4">
        <ApprovalPanel runId={item.run_id} />
      </div>
    </Card>
  );
}

export default function ApprovalsPage() {
  const pending = useQuery({ queryKey: ["approvals"], queryFn: api.pendingApprovals, refetchInterval: 3_000 });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Approvals inbox</h1>
        <p className="mt-1 text-sm text-slate-600">
          Purchase orders above the approval threshold wait here. Approving sends the PO and the visit is booked after
          delivery. Rejecting books an inspection visit and flags the ticket for manual procurement.
        </p>
      </div>
      <ErrorNote error={pending.error} />
      {pending.isPending && <p className="text-sm text-slate-500">Loading…</p>}
      {pending.data?.length === 0 && (
        <Card>
          <p className="text-sm text-slate-600">Nothing is waiting for approval.</p>
        </Card>
      )}
      {pending.data?.map((item) => (
        <ApprovalCard key={item.run_id} item={item} />
      ))}
    </div>
  );
}
