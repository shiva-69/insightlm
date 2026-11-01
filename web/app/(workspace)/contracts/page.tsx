"use client";

import clsx from "clsx";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { QueryError } from "@/components/query-state";
import { useToast } from "@/components/toast";
import { Button, Empty, Field, Input, Panel, Spinner, Tag } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import type { ClauseFinding, ContractReview, Playbook, ReviewFlag, Severity } from "@/lib/types";

const FINDING_LABEL: Record<ClauseFinding, string> = {
  meets: "Meets",
  deviates: "Deviates",
  missing: "Not found",
};
const FLAG_LABEL: Record<ReviewFlag, string> = {
  critical_gap: "Critical gap",
  major_gap: "Major gap",
  low_confidence: "Low confidence",
};

/* A starting point, not a recommendation: counsel edit it, and nothing is reviewed
   against it until someone other than its author approves it. */
const TEMPLATE: { clause: string; standard: string; severity: Severity; evidence_hint: string }[] = [
  {
    clause: "Limitation of liability",
    standard: "Aggregate liability capped at 12 months' fees; breach of confidentiality uncapped.",
    severity: "critical",
    evidence_hint: "liability, aggregate, cap",
  },
  {
    clause: "Termination for convenience",
    standard: "Either party may terminate on 30 days' written notice.",
    severity: "major",
    evidence_hint: "terminate, convenience, notice",
  },
  {
    clause: "Payment terms",
    standard: "Invoices payable within 30 days of receipt.",
    severity: "major",
    evidence_hint: "invoice, payable, days",
  },
  {
    clause: "Data protection",
    standard: "Personal data processed only on our documented instructions, under GDPR Article 28 terms.",
    severity: "critical",
    evidence_hint: "personal data, processor, GDPR",
  },
  {
    clause: "Governing law",
    standard: "Laws of England and Wales.",
    severity: "minor",
    evidence_hint: "governed, laws",
  },
];

function failure(notify: ReturnType<typeof useToast>["notify"], fallback: string) {
  return (error: unknown) => {
    const e = error as ApiError;
    notify(e.message || fallback, { tone: "error", detail: e.correlationId ? `ref ${e.correlationId}` : undefined });
  };
}

export default function ContractsPage() {
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const playbooks = useQuery({ queryKey: ["playbooks"], queryFn: api.playbooks });
  const reviews = useQuery({ queryKey: ["contract-reviews"], queryFn: api.contractReviews });
  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents });

  const [drafting, setDrafting] = useState(false);
  const [name, setName] = useState("Standard MSA");
  const [positions, setPositions] = useState(TEMPLATE);

  const [title, setTitle] = useState("");
  const [playbookId, setPlaybookId] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  const propose = useMutation({
    mutationFn: () => api.proposePlaybook({ name: name.trim(), positions }),
    onSuccess: (playbook) => {
      queryClient.invalidateQueries({ queryKey: ["playbooks"] });
      setDrafting(false);
      notify(`${playbook.name} v${playbook.version} proposed`, { detail: "Counsel must approve it before use." });
    },
    onError: failure(notify, "Could not propose the playbook"),
  });
  const approve = useMutation({
    mutationFn: (id: string) => api.approvePlaybook(id),
    onSuccess: (playbook) => {
      queryClient.invalidateQueries({ queryKey: ["playbooks"] });
      notify(`${playbook.name} v${playbook.version} approved`);
    },
    onError: failure(notify, "Could not approve the playbook"),
  });
  const review = useMutation({
    mutationFn: () => api.reviewContract({ title: title.trim(), playbook_id: playbookId, document_ids: picked }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["contract-reviews"] });
      setOpen(result.id);
      setTitle("");
      setPicked([]);
      notify(result.summary.needs_counsel ? "Reviewed: needs counsel" : "Reviewed", {
        detail: `${result.summary.meets} meet, ${result.summary.deviates} deviate, ${result.summary.missing} not found.`,
      });
    },
    onError: failure(notify, "Could not review the contract"),
  });

  const approved = (playbooks.data ?? []).filter((p) => p.status === "approved");
  const byId = new Map((playbooks.data ?? []).map((p) => [p.id, p]));

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-[56rem] px-7 py-8">
        <header className="mb-7">
          <h1 className="font-serif text-[26px] leading-tight">Contracts</h1>
          <p className="mt-1.5 max-w-[40rem] text-[13.5px] text-[var(--ink-3)]">
            Each contract is checked clause by clause against a playbook counsel approved. Every
            finding quotes the passage it rests on; critical gaps go to counsel.
          </p>
        </header>

        <section className="mb-8">
          <div className="mb-2.5 flex items-center justify-between">
            <h2 className="text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">Playbooks</h2>
            {!drafting && (
              <Button variant="quiet" size="sm" onClick={() => setDrafting(true)}>
                + New playbook
              </Button>
            )}
          </div>

          {drafting && (
            <Panel className="mb-3 p-4">
              <Field label="Name">
                <Input value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <ul className="mt-3 space-y-2">
                {positions.map((p, i) => (
                  <li key={i} className="grid gap-2 sm:grid-cols-[12rem_minmax(0,1fr)_7rem]">
                    <Input
                      aria-label={`Clause ${i + 1}`}
                      value={p.clause}
                      onChange={(e) => setPositions((all) => all.map((x, j) => (j === i ? { ...x, clause: e.target.value } : x)))}
                    />
                    <Input
                      aria-label={`Standard for clause ${i + 1}`}
                      value={p.standard}
                      onChange={(e) => setPositions((all) => all.map((x, j) => (j === i ? { ...x, standard: e.target.value } : x)))}
                    />
                    <select
                      aria-label={`Severity for clause ${i + 1}`}
                      value={p.severity}
                      onChange={(e) =>
                        setPositions((all) => all.map((x, j) => (j === i ? { ...x, severity: e.target.value as Severity } : x)))
                      }
                      className="rounded-[var(--radius-sm)] border border-[var(--rule)] bg-[var(--surface)] px-2 text-[13px]"
                    >
                      <option value="critical">Critical</option>
                      <option value="major">Major</option>
                      <option value="minor">Minor</option>
                    </select>
                  </li>
                ))}
              </ul>
              <div className="mt-3 flex justify-end gap-2">
                <Button variant="quiet" onClick={() => setDrafting(false)}>
                  Cancel
                </Button>
                <Button variant="solid" loading={propose.isPending} onClick={() => propose.mutate()}>
                  Propose
                </Button>
              </div>
            </Panel>
          )}

          {playbooks.isPending ? (
            <Spinner />
          ) : playbooks.isError ? (
            <QueryError error={playbooks.error} retry={() => playbooks.refetch()} what="playbooks" />
          ) : playbooks.data.length === 0 ? (
            !drafting && (
              <Panel>
                <Empty title="No playbooks yet" body="A playbook sets the position each clause should take. Start from the template and edit it." />
              </Panel>
            )
          ) : (
            <ul className="divide-y divide-[var(--rule)] rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--surface)]">
              {playbooks.data.map((p) => (
                <PlaybookRow key={p.id} playbook={p} approving={approve.isPending && approve.variables === p.id} onApprove={() => approve.mutate(p.id)} />
              ))}
            </ul>
          )}
        </section>

        <section className="mb-8">
          <h2 className="mb-2.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">Review a contract</h2>
          <Panel className="p-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Title">
                <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Acme Ltd master services agreement" />
              </Field>
              <Field label="Playbook" hint={approved.length === 0 ? "Approve a playbook first." : undefined}>
                <select
                  value={playbookId}
                  onChange={(e) => setPlaybookId(e.target.value)}
                  className="h-9 w-full rounded-[var(--radius-sm)] border border-[var(--rule)] bg-[var(--surface)] px-2 text-[13px]"
                >
                  <option value="">Choose…</option>
                  {approved.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} v{p.version}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <fieldset className="mt-3">
              <legend className="mb-1.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">The contract&apos;s documents</legend>
              <ul className="max-h-40 space-y-1 overflow-auto">
                {(documents.data ?? []).map((d) => (
                  <li key={d.id}>
                    <label className="flex cursor-pointer items-center gap-2.5 text-[13px]">
                      <input
                        type="checkbox"
                        className="accent-[var(--ink)]"
                        checked={picked.includes(d.id)}
                        onChange={() => setPicked((cur) => (cur.includes(d.id) ? cur.filter((x) => x !== d.id) : [...cur, d.id].slice(0, 10)))}
                      />
                      <span className="truncate">{d.title}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </fieldset>
            <div className="mt-3 flex justify-end">
              <Button
                variant="solid"
                disabled={!title.trim() || !playbookId || picked.length === 0}
                loading={review.isPending}
                onClick={() => review.mutate()}
              >
                Review
              </Button>
            </div>
          </Panel>
        </section>

        <section>
          <h2 className="mb-2.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">Reviews</h2>
          {reviews.isPending ? (
            <Spinner />
          ) : reviews.isError ? (
            <QueryError error={reviews.error} retry={() => reviews.refetch()} what="reviews" />
          ) : reviews.data.length === 0 ? (
            <Panel>
              <Empty title="No reviews yet" body="Choose an approved playbook and the contract's documents above." />
            </Panel>
          ) : (
            <ul className="space-y-2">
              {reviews.data.map((r) => (
                <ReviewCard key={r.id} review={r} playbook={byId.get(r.playbook_id)} open={open === r.id} onToggle={() => setOpen(open === r.id ? null : r.id)} />
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function PlaybookRow({ playbook, approving, onApprove }: { playbook: Playbook; approving: boolean; onApprove: () => void }) {
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[14px] font-medium">
          {playbook.name} <span className="data">v{playbook.version}</span>
        </span>
        <span className="text-[12px] text-[var(--ink-3)]">{playbook.positions.length} positions</span>
      </span>
      <Tag tone={playbook.status === "approved" ? "positive" : "neutral"}>{playbook.status}</Tag>
      {playbook.status === "draft" && (
        <Button variant="outline" size="sm" loading={approving} onClick={onApprove}>
          Approve
        </Button>
      )}
    </li>
  );
}

function ReviewCard({
  review,
  playbook,
  open,
  onToggle,
}: {
  review: ContractReview;
  playbook: Playbook | undefined;
  open: boolean;
  onToggle: () => void;
}) {
  const positions = new Map((playbook?.positions ?? []).map((p) => [p.id, p]));
  const { summary } = review;
  return (
    <li>
      <Panel>
        <button onClick={onToggle} className="flex w-full items-center gap-3 px-4 py-3 text-left" aria-expanded={open}>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[14px] font-medium">{review.title}</span>
            <span className="text-[12px] text-[var(--ink-3)]">
              {summary.meets} meet · {summary.deviates} deviate · {summary.missing} not found
            </span>
          </span>
          {summary.flags.map((f) => (
            <Tag key={f} tone="caution">
              {FLAG_LABEL[f]}
            </Tag>
          ))}
          {summary.needs_counsel && <Tag tone="caution">Needs counsel</Tag>}
        </button>
        {open && (
          <ol className="divide-y divide-[var(--rule)] border-t border-[var(--rule)]">
            {review.findings.map((f) => {
              const position = positions.get(f.position_id);
              return (
                <li key={f.position_id} className="px-4 py-3">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[13.5px] font-medium">{position?.clause ?? "Clause"}</span>
                    <Tag tone={f.finding === "meets" ? "positive" : f.finding === "deviates" ? "caution" : "neutral"}>
                      {FINDING_LABEL[f.finding]}
                    </Tag>
                  </div>
                  {position && <p className="mt-0.5 text-[12px] text-[var(--ink-3)]">Standard: {position.standard}</p>}
                  <p className="mt-1.5 text-[13px] text-[var(--ink-2)]">{f.rationale}</p>
                  {f.evidence.map((e) => (
                    <blockquote
                      key={e.label}
                      className={clsx(
                        "mt-2 rounded-[var(--radius-sm)] border-l-2 border-[var(--evidence-edge)] bg-[var(--surface-sunk)] px-3 py-2 text-[12.5px]",
                      )}
                    >
                      {e.quoted_text}
                      <span className="data mt-1 block">
                        {e.document_title} · p{e.page + 1}
                      </span>
                    </blockquote>
                  ))}
                </li>
              );
            })}
          </ol>
        )}
      </Panel>
    </li>
  );
}
