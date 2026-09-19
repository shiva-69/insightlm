"use client";

import clsx from "clsx";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useToast } from "@/components/toast";
import { Button, Empty, Panel, Spinner, Tag } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import type { AuditEntry, ChainVerification } from "@/lib/types";

const ACTION_LABEL: Record<string, string> = {
  "tenant.created": "Workspace created",
  "user.registered": "Account registered",
  "user.authenticated": "Signed in",
  "invitation.created": "Invitation issued",
  "invitation.accepted": "Invitation accepted",
  "invitation.revoked": "Invitation revoked",
  "membership.role_changed": "Role changed",
  "membership.revoked": "Member removed",
  "document.uploaded": "Document uploaded",
};

export default function AuditPage() {
  const { notify } = useToast();
  const entries = useQuery({ queryKey: ["audit"], queryFn: () => api.auditEntries(200) });

  const verify = useMutation({
    mutationFn: api.verifyChain,
    onError: (error) => {
      const failure = error as ApiError;
      notify(failure.message || "Verification failed", { tone: "error" });
    },
  });

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-[56rem] px-7 py-8">
        <header className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="font-serif text-[25px] leading-tight tracking-[-0.012em]">Audit</h1>
            <p className="mt-1 max-w-[34rem] text-[13px] leading-relaxed text-[var(--ink-3)]">
              Every entry commits to the one before it. Altering a past record breaks
              every hash that follows, and verification says exactly where.
            </p>
          </div>
          <Button onClick={() => verify.mutate()} loading={verify.isPending}>
            Verify chain
          </Button>
        </header>

        {verify.data && <VerificationResult result={verify.data} />}

        {entries.isLoading && (
          <p className="flex items-center gap-2 py-8 text-[13px] text-[var(--ink-3)]">
            <Spinner /> Reading the log…
          </p>
        )}

        {entries.data?.length === 0 && (
          <Empty title="Nothing logged yet" body="Actions appear here as they happen." />
        )}

        {entries.data && entries.data.length > 0 && (
          <ol className="mt-5 space-y-0">
            {entries.data.map((entry, index) => (
              <EntryRow key={entry.id} entry={entry} last={index === entries.data.length - 1} />
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function VerificationResult({ result }: { result: ChainVerification }) {
  return (
    <Panel
      className={clsx(
        "p-4",
        result.is_intact ? "border-[var(--positive)]/40" : "border-[var(--caution)]/50",
      )}
    >
      <div className="flex items-center gap-2.5">
        <Tag tone={result.is_intact ? "positive" : "caution"}>
          {result.is_intact ? "Intact" : "Broken"}
        </Tag>
        <span className="text-[13px] text-[var(--ink-2)]">
          {result.entries_checked} {result.entries_checked === 1 ? "entry" : "entries"} verified
        </span>
      </div>
      {!result.is_intact && (
        <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--caution)]">
          {result.reason}
          {result.broken_at && (
            <span className="data ml-1 text-[var(--caution)]">at {result.broken_at}</span>
          )}
        </p>
      )}
    </Panel>
  );
}

function EntryRow({ entry, last }: { entry: AuditEntry; last: boolean }) {
  return (
    <li className="relative flex gap-3.5 pl-1">
      {/* The chain, drawn. Each entry links visually to the next, which is what the
          hashes do underneath. */}
      <div className="flex flex-col items-center pt-[7px]">
        <span className="size-1.5 shrink-0 rounded-full bg-[var(--rule-strong)]" aria-hidden />
        {!last && <span className="w-px flex-1 bg-[var(--rule)]" aria-hidden />}
      </div>

      <div className="min-w-0 flex-1 pb-4">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-[13px] text-[var(--ink)]">
            {ACTION_LABEL[entry.action] ?? entry.action}
          </span>
          <span className="data">{entry.subject_type}</span>
          <span className="data ml-auto">
            {new Date(entry.occurred_at).toLocaleString(undefined, {
              day: "numeric",
              month: "short",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        </div>
        <div className="data mt-1 flex flex-wrap items-center gap-x-2">
          <span title="Hash of the preceding entry">← {entry.prev_hash.slice(0, 10)}</span>
          <span className="text-[var(--ink-3)]">·</span>
          <span title="This entry's hash">{entry.entry_hash.slice(0, 10)}</span>
        </div>
      </div>
    </li>
  );
}
