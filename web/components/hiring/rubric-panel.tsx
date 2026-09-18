"use client";

import clsx from "clsx";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useToast } from "@/components/toast";
import { Button, Input, Panel, Tag } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { currentClaims } from "@/lib/auth";
import type { CriterionInput, CriterionKind, Rubric } from "@/lib/types";

export const KIND_LABEL: Record<CriterionKind, string> = {
  must_have: "Must have",
  strong_preference: "Strong preference",
  nice_to_have: "Nice to have",
};

const KINDS: CriterionKind[] = ["must_have", "strong_preference", "nice_to_have"];

/* Mirrors the server's permission table. Only used to decide what to offer; the
   server makes the decision. */
const CAN_PROPOSE = new Set(["owner", "recruiter"]);
const CAN_APPROVE = new Set(["owner", "hiring_manager"]);

const BLANK: CriterionInput = {
  label: "",
  kind: "must_have",
  weight: 1,
  evidence_hint: "",
  disqualifying: false,
};

export function RubricPanel({
  requisitionId,
  rubrics,
}: {
  requisitionId: string;
  rubrics: Rubric[];
}) {
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const claims = currentClaims();
  const [editing, setEditing] = useState(false);

  const approved = rubrics.find((r) => r.status === "approved") ?? null;
  const draft =
    [...rubrics].filter((r) => r.status === "draft").sort((a, b) => b.version - a.version)[0] ??
    null;
  const history = rubrics.filter((r) => r.status === "superseded").length;

  const approve = useMutation({
    mutationFn: (rubricId: string) => api.approveRubric(rubricId),
    onSuccess: (rubric) => {
      queryClient.invalidateQueries({ queryKey: ["requisition", requisitionId] });
      notify(`Rubric v${rubric.version} approved`, {
        detail: "Candidates can now be assessed against it.",
      });
    },
    onError: (error) => fail(error, "Could not approve the rubric"),
  });

  function fail(error: unknown, fallback: string) {
    const failure = error as ApiError;
    notify(failure.message || fallback, {
      tone: "error",
      detail: failure.correlationId ? `ref ${failure.correlationId}` : undefined,
    });
  }

  if (editing || rubrics.length === 0) {
    return (
      <RubricEditor
        requisitionId={requisitionId}
        start={(draft ?? approved)?.criteria ?? null}
        canPropose={claims ? CAN_PROPOSE.has(claims.role) : false}
        onDone={() => setEditing(false)}
        onCancel={rubrics.length > 0 ? () => setEditing(false) : undefined}
        onError={(e) => fail(e, "Could not propose the rubric")}
      />
    );
  }

  const selfApproval =
    draft !== null && claims?.userId === draft.proposed_by && claims.role !== "owner";
  const mayApprove = claims ? CAN_APPROVE.has(claims.role) : false;

  return (
    <div className="space-y-3">
      {draft && (
        <Panel className="border-[var(--rule-strong)] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Tag>Draft v{draft.version}</Tag>
              <span className="text-[12.5px] text-[var(--ink-2)]">
                {selfApproval
                  ? "Waiting for someone other than you to approve it."
                  : mayApprove
                    ? "Review the criteria below, then approve."
                    : "Waiting for a hiring manager to approve it."}
              </span>
            </div>
            {mayApprove && !selfApproval && (
              <Button
                variant="solid"
                size="sm"
                loading={approve.isPending}
                onClick={() => approve.mutate(draft.id)}
              >
                Approve v{draft.version}
              </Button>
            )}
          </div>
          <CriteriaLedger rubric={draft} className="mt-3.5" />
        </Panel>
      )}

      {approved ? (
        <Panel className="p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Tag tone="positive">Approved v{approved.version}</Tag>
              {approved.approved_at && (
                <span className="text-[12px] text-[var(--ink-3)]">
                  {new Date(approved.approved_at).toLocaleDateString(undefined, {
                    day: "numeric",
                    month: "short",
                  })}
                  {history > 0 && ` · supersedes ${history} earlier version${history > 1 ? "s" : ""}`}
                </span>
              )}
            </div>
            {!draft && claims && CAN_PROPOSE.has(claims.role) && (
              <Button variant="quiet" size="sm" onClick={() => setEditing(true)}>
                Propose a revision
              </Button>
            )}
          </div>
          {!draft && <CriteriaLedger rubric={approved} className="mt-3.5" />}
        </Panel>
      ) : (
        !draft && (
          <p className="text-[12.5px] text-[var(--ink-3)]">No rubric has been approved yet.</p>
        )
      )}
    </div>
  );
}

/* A ledger rather than cards: criteria are compared with each other, so they are set
   in aligned columns, and weight is a bar you can compare at a glance. */
function CriteriaLedger({ rubric, className }: { rubric: Rubric; className?: string }) {
  const total = rubric.criteria.reduce((sum, c) => sum + c.weight, 0);
  return (
    <ol className={clsx("divide-y divide-[var(--rule)]", className)}>
      {rubric.criteria.map((criterion) => (
        <li
          key={criterion.id}
          className="grid grid-cols-[minmax(0,1fr)_7.5rem_4.5rem] items-baseline gap-4 py-2"
        >
          <span className="min-w-0">
            <span className="text-[13.5px]">{criterion.label}</span>
            {criterion.disqualifying && (
              <span className="ml-2 text-[11px] text-[var(--caution)]">flags if absent</span>
            )}
            {criterion.evidence_hint && (
              <span className="mt-0.5 block truncate text-[12px] text-[var(--ink-3)]">
                Look for: {criterion.evidence_hint}
              </span>
            )}
          </span>
          <span className="text-[12px] text-[var(--ink-2)]">{KIND_LABEL[criterion.kind]}</span>
          <span className="flex items-center gap-2" title={`weight ${criterion.weight}`}>
            <span className="h-1 flex-1 overflow-hidden rounded-full bg-[var(--surface-sunk)]">
              <span
                className="block h-full rounded-full bg-[var(--ink-2)]"
                style={{ width: `${(criterion.weight / total) * 100}%` }}
              />
            </span>
            <span className="data w-7 text-right">{Math.round((criterion.weight / total) * 100)}%</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

function RubricEditor({
  requisitionId,
  start,
  canPropose,
  onDone,
  onCancel,
  onError,
}: {
  requisitionId: string;
  start: CriterionInput[] | null;
  canPropose: boolean;
  onDone: () => void;
  onCancel?: () => void;
  onError: (error: unknown) => void;
}) {
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<CriterionInput[]>(
    start?.map(({ label, kind, weight, evidence_hint, disqualifying }) => ({
      label,
      kind,
      weight,
      evidence_hint,
      disqualifying,
    })) ?? [{ ...BLANK }],
  );

  const propose = useMutation({
    mutationFn: () =>
      api.proposeRubric(
        requisitionId,
        rows.map((r) => ({ ...r, label: r.label.trim(), evidence_hint: r.evidence_hint.trim() })),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["requisition", requisitionId] });
      onDone();
    },
    onError,
  });

  function update(index: number, patch: Partial<CriterionInput>) {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  const labels = rows.map((r) => r.label.trim().toLowerCase());
  const duplicate = labels.some((l, i) => l && labels.indexOf(l) !== i);
  const valid = rows.length > 0 && rows.every((r) => r.label.trim()) && !duplicate;

  if (!canPropose) {
    return (
      <Panel className="p-4 text-[12.5px] text-[var(--ink-3)]">
        No rubric yet. A recruiter or the owner proposes one; a hiring manager approves it.
      </Panel>
    );
  }

  return (
    <Panel className="p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[13.5px] font-medium">{start ? "Propose a revision" : "Propose a rubric"}</h3>
        <span className="text-[12px] text-[var(--ink-3)]">
          It becomes usable once someone else approves it.
        </span>
      </div>

      <ol className="mt-3.5 space-y-2.5">
        {rows.map((row, index) => (
          <li
            key={index}
            className="rounded-[var(--radius-sm)] border border-[var(--rule)] bg-[var(--paper)] p-3"
          >
            <div className="flex gap-2">
              <Input
                value={row.label}
                onChange={(e) => update(index, { label: e.target.value })}
                placeholder="What the candidate should show, e.g. Python backend services"
                aria-label={`Criterion ${index + 1}`}
              />
              <Button
                variant="quiet"
                className="w-9 shrink-0 px-0"
                aria-label="Remove criterion"
                disabled={rows.length === 1}
                onClick={() => setRows((current) => current.filter((_, i) => i !== index))}
              >
                ×
              </Button>
            </div>

            <div className="mt-2.5 flex flex-wrap items-center gap-x-5 gap-y-2.5">
              <div role="radiogroup" aria-label="Importance" className="flex rounded-[var(--radius-sm)] border border-[var(--rule-strong)] p-0.5">
                {KINDS.map((kind) => (
                  <button
                    key={kind}
                    role="radio"
                    aria-checked={row.kind === kind}
                    onClick={() =>
                      update(index, {
                        kind,
                        disqualifying: kind === "must_have" ? row.disqualifying : false,
                      })
                    }
                    className={clsx(
                      "h-6 rounded-[4px] px-2 text-[12px] transition-colors",
                      row.kind === kind
                        ? "bg-[var(--ink)] text-[var(--paper)]"
                        : "text-[var(--ink-3)] hover:text-[var(--ink)]",
                    )}
                  >
                    {KIND_LABEL[kind]}
                  </button>
                ))}
              </div>

              <label className="flex items-center gap-2 text-[12px] text-[var(--ink-3)]">
                Weight
                <input
                  type="range"
                  min={0.1}
                  max={1}
                  step={0.1}
                  value={row.weight}
                  onChange={(e) => update(index, { weight: Number(e.target.value) })}
                  className="w-24 accent-[var(--ink)]"
                />
                <span className="data w-6">{row.weight.toFixed(1)}</span>
              </label>

              {row.kind === "must_have" && (
                <label className="flex items-center gap-1.5 text-[12px] text-[var(--ink-3)]">
                  <input
                    type="checkbox"
                    checked={row.disqualifying}
                    onChange={(e) => update(index, { disqualifying: e.target.checked })}
                    className="accent-[var(--ink)]"
                  />
                  Flag for review if absent
                </label>
              )}
            </div>

            <Input
              value={row.evidence_hint}
              onChange={(e) => update(index, { evidence_hint: e.target.value })}
              placeholder="What counts as evidence (optional) — e.g. services built and run in production"
              className="mt-2.5 h-8 text-[12.5px]"
              aria-label="Evidence hint"
            />
          </li>
        ))}
      </ol>

      {duplicate && (
        <p className="mt-2 text-[12px] text-[var(--caution)]">Two criteria have the same name.</p>
      )}

      <div className="mt-3.5 flex flex-wrap items-center justify-between gap-2">
        <Button
          variant="quiet"
          size="sm"
          disabled={rows.length >= 25}
          onClick={() => setRows((current) => [...current, { ...BLANK, kind: "strong_preference", weight: 0.5 }])}
        >
          + Add criterion
        </Button>
        <div className="flex gap-2">
          {onCancel && (
            <Button variant="quiet" onClick={onCancel}>
              Cancel
            </Button>
          )}
          <Button variant="solid" disabled={!valid} loading={propose.isPending} onClick={() => propose.mutate()}>
            Propose for approval
          </Button>
        </div>
      </div>
    </Panel>
  );
}
