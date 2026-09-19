"use client";

import clsx from "clsx";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { QueryError } from "@/components/query-state";
import { useToast } from "@/components/toast";
import { Button, Empty, Field, Input, Panel, Spinner, Tag } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { STAGE_LABEL } from "@/lib/labels";
import { BulkIntake } from "./bulk-intake";
import type { AssessmentFlag, ShortlistEntry } from "@/lib/types";

export const FLAG_LABEL: Record<AssessmentFlag, string> = {
  missing_must_have: "Must-have not found",
  disqualifying_absent: "Flagged criterion absent",
  low_confidence: "Low confidence",
};

export function CandidatesPanel({
  requisitionId,
  hasApprovedRubric,
  onOpen,
}: {
  requisitionId: string;
  hasApprovedRubric: boolean;
  onOpen: (applicationId: string) => void;
}) {
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [bulk, setBulk] = useState(false);

  const shortlist = useQuery({
    queryKey: ["shortlist", requisitionId],
    queryFn: () => api.shortlist(requisitionId),
  });

  const assess = useMutation({
    mutationFn: (applicationId: string) => api.assess(applicationId),
    onSuccess: (assessment) => {
      queryClient.invalidateQueries({ queryKey: ["shortlist", requisitionId] });
      notify(`Assessed: ${assessment.score.toFixed(0)} / 100`, {
        detail: assessment.needs_human_review
          ? "Flagged for a person to review — see why."
          : `${assessment.judgments.length} criteria, each with its evidence.`,
      });
    },
    onError: (error) => {
      const failure = error as ApiError;
      notify(failure.message || "Assessment failed", {
        tone: "error",
        detail: failure.correlationId ? `ref ${failure.correlationId}` : undefined,
      });
    },
  });

  const entries = shortlist.data ?? [];

  return (
    <div>
      <div className="mb-2.5 flex items-center justify-between">
        <h2 className="text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
          Candidates{entries.length > 0 && ` · ${entries.length}`}
        </h2>
        {!adding && !bulk && (
          <div className="flex gap-1">
            <Button variant="quiet" size="sm" onClick={() => setBulk(true)}>
              Upload CVs
            </Button>
            <Button variant="quiet" size="sm" onClick={() => setAdding(true)}>
              + Add candidate
            </Button>
          </div>
        )}
      </div>

      {bulk && <BulkIntake requisitionId={requisitionId} onClose={() => setBulk(false)} />}

      {adding && (
        <AddCandidate
          requisitionId={requisitionId}
          onClose={() => setAdding(false)}
        />
      )}

      {shortlist.isPending ? (
        <div className="flex justify-center py-10 text-[var(--ink-3)]">
          <Spinner />
        </div>
      ) : shortlist.isError ? (
        <QueryError error={shortlist.error} retry={() => shortlist.refetch()} what="candidates" />
      ) : entries.length === 0 ? (
        !adding &&
        !bulk && (
          <Panel>
            <Empty
              title="No candidates yet"
              body="Upload their CVs to the Library, then add them here. Assessment reads only the documents you attach."
              action={
                <Button variant="outline" onClick={() => setAdding(true)}>
                  Add a candidate
                </Button>
              }
            />
          </Panel>
        )
      ) : (
        <ol className="divide-y divide-[var(--rule)] rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--surface)]">
          {entries.map((entry, index) => (
            <CandidateRow
              key={entry.application.id}
              entry={entry}
              rank={entry.assessment ? index + 1 : null}
              canAssess={hasApprovedRubric}
              assessing={assess.isPending && assess.variables === entry.application.id}
              onAssess={() => assess.mutate(entry.application.id)}
              onOpen={() => onOpen(entry.application.id)}
            />
          ))}
        </ol>
      )}

      {!hasApprovedRubric && entries.length > 0 && (
        <p className="mt-2.5 text-[12px] text-[var(--ink-3)]">
          Assessment unlocks once a rubric is approved.
        </p>
      )}
    </div>
  );
}

function CandidateRow({
  entry,
  rank,
  canAssess,
  assessing,
  onAssess,
  onOpen,
}: {
  entry: ShortlistEntry;
  rank: number | null;
  canAssess: boolean;
  assessing: boolean;
  onAssess: () => void;
  onOpen: () => void;
}) {
  const { candidate, assessment } = entry;
  return (
    <li className="grid grid-cols-[1.5rem_minmax(0,1fr)_9rem_auto] items-center gap-4 px-4 py-3">
      <span className="data text-right">{rank ?? "—"}</span>

      <span className="min-w-0">
        <Link
          href={`/applications/${entry.application.id}`}
          className="block truncate text-[14px] font-medium hover:underline"
        >
          {candidate.name}
        </Link>
        <span className="mt-1 flex flex-wrap items-center gap-1">
          <Tag>{STAGE_LABEL[entry.application.stage]}</Tag>
          {entry.overdue && <Tag tone="caution">Overdue</Tag>}
          {assessment ? (
            assessment.flags.length > 0 ? (
              assessment.flags.map((flag) => (
                <Tag key={flag} tone="caution">
                  {FLAG_LABEL[flag]}
                </Tag>
              ))
            ) : (
              <span className="text-[12px] text-[var(--ink-3)]">No flags</span>
            )
          ) : (
            <span className="text-[12px] text-[var(--ink-3)]">
              Not assessed · {candidate.document_ids.length} document
              {candidate.document_ids.length === 1 ? "" : "s"}
            </span>
          )}
        </span>
      </span>

      {assessment ? (
        <span className="flex items-center gap-2.5" title={`${assessment.score} / 100`}>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--surface-sunk)]">
            <span
              className="block h-full rounded-full bg-[var(--ink)]"
              style={{ width: `${assessment.score}%` }}
            />
          </span>
          <span className="w-8 text-right font-serif text-[17px] leading-none">
            {Math.round(assessment.score)}
          </span>
        </span>
      ) : (
        <span />
      )}

      <span className="flex justify-end gap-1.5">
        {assessment && (
          <Button variant="outline" size="sm" onClick={onOpen}>
            Evidence
          </Button>
        )}
        <Button
          variant={assessment ? "quiet" : "solid"}
          size="sm"
          disabled={!canAssess}
          loading={assessing}
          onClick={onAssess}
        >
          {assessment ? "Re-assess" : "Assess"}
        </Button>
      </span>
    </li>
  );
}

function AddCandidate({ requisitionId, onClose }: { requisitionId: string; onClose: () => void }) {
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [picked, setPicked] = useState<string[]>([]);

  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents });
  const ready = documents.data ?? [];

  const add = useMutation({
    mutationFn: () =>
      api.addCandidate(requisitionId, {
        name: name.trim(),
        email: email.trim() || undefined,
        document_ids: picked,
      }),
    onSuccess: ({ candidate }) => {
      queryClient.invalidateQueries({ queryKey: ["shortlist", requisitionId] });
      notify(`${candidate.name} added`);
      onClose();
    },
    onError: (error) => {
      const failure = error as ApiError;
      notify(failure.message || "Could not add the candidate", {
        tone: "error",
        detail: failure.correlationId ? `ref ${failure.correlationId}` : undefined,
      });
    },
  });

  function toggle(id: string) {
    setPicked((current) =>
      current.includes(id) ? current.filter((d) => d !== id) : [...current, id].slice(0, 10),
    );
  }

  return (
    <Panel className="mb-3 p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name">
          <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Asha Rao" />
        </Field>
        <Field label="Email" hint="Optional">
          <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="asha@example.com" />
        </Field>
      </div>

      <fieldset className="mt-3.5">
        <legend className="mb-1.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
          Their documents
        </legend>
        {documents.isPending ? (
          <Spinner />
        ) : documents.isError ? (
          <QueryError error={documents.error} retry={() => documents.refetch()} what="the library" />
        ) : ready.length === 0 ? (
          <p className="text-[12.5px] text-[var(--ink-3)]">
            The Library is empty. Upload the CV there first.
          </p>
        ) : (
          <ul className="max-h-48 space-y-1 overflow-auto">
            {ready.map((document) => {
              const on = picked.includes(document.id);
              return (
                <li key={document.id}>
                  <label
                    className={clsx(
                      "flex cursor-pointer items-center gap-2.5 rounded-[var(--radius-sm)] border px-2.5 py-1.5 transition-colors",
                      on ? "border-[var(--ink)] bg-[var(--surface-sunk)]" : "border-[var(--rule)] hover:border-[var(--rule-strong)]",
                    )}
                  >
                    <input type="checkbox" checked={on} onChange={() => toggle(document.id)} className="accent-[var(--ink)]" />
                    <span className="truncate text-[13px]">{document.title}</span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}
      </fieldset>

      <div className="mt-3.5 flex justify-end gap-2">
        <Button variant="quiet" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="solid"
          disabled={!name.trim() || picked.length === 0}
          loading={add.isPending}
          onClick={() => add.mutate()}
        >
          Add candidate
        </Button>
      </div>
    </Panel>
  );
}
