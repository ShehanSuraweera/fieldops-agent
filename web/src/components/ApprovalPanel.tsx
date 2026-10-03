"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ErrorNote, Spinner } from "@/components/ui";

/** Approve or reject a run's purchase order. `onDecided` runs after the agent accepts the decision. */
export function ApprovalPanel({
  runId,
  now,
  onDecided,
}: {
  runId: string;
  now?: string;
  onDecided?: (decision: "approve" | "reject") => void;
}) {
  const queryClient = useQueryClient();
  const [comment, setComment] = useState("");
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") => api.decide(runId, decision, comment, now),
    onSuccess: (_, decision) => {
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["metrics"] });
      onDecided?.(decision);
    },
  });
  const pending = decide.isPending ? decide.variables : null;

  return (
    <div className="space-y-2">
      <textarea
        rows={2}
        value={comment}
        onChange={(event) => setComment(event.target.value)}
        placeholder="Comment for the ticket (optional)"
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-sky-500 focus:outline-none"
      />
      <ErrorNote error={decide.error} />
      <div className="flex gap-2">
        <button
          type="button"
          disabled={decide.isPending || decide.isSuccess}
          onClick={() => decide.mutate("approve")}
          className="inline-flex items-center gap-2 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
        >
          {pending === "approve" && <Spinner />} Approve PO
        </button>
        <button
          type="button"
          disabled={decide.isPending || decide.isSuccess}
          onClick={() => decide.mutate("reject")}
          className="inline-flex items-center gap-2 rounded-md bg-rose-600 px-4 py-2 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-60"
        >
          {pending === "reject" && <Spinner />} Reject PO
        </button>
        {decide.isSuccess && (
          <span className="self-center text-sm text-slate-600">
            {decide.variables === "approve" ? "Approved" : "Rejected"}, so the run is resuming.
          </span>
        )}
      </div>
    </div>
  );
}
