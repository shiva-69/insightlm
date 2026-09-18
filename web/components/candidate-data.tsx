"use client";

import { useMutation } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useToast } from "@/components/toast";
import { Button, Input, Panel, Tag } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import type { ErasureReport } from "@/lib/types";

/* Owner-only. Two rights a candidate can exercise: to see what is held about them, and
   to have it erased. Erasure is irreversible, so it asks for the name to be typed. */
export function CandidateData({
  candidateId,
  name,
  returnTo,
}: {
  candidateId: string;
  name: string;
  returnTo: string;
}) {
  const router = useRouter();
  const { notify } = useToast();
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const [report, setReport] = useState<ErasureReport | null>(null);

  async function download() {
    try {
      const record = await api.candidateRecord(candidateId);
      const blob = new Blob([JSON.stringify(record, null, 2)], { type: "application/json" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `candidate-record-${candidateId}.json`;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (error) {
      notify((error as ApiError).message || "Could not export the record", { tone: "error" });
    }
  }

  const erase = useMutation({
    mutationFn: () => api.eraseCandidate(candidateId),
    onSuccess: setReport,
    onError: (error) => notify((error as ApiError).message || "Erasure failed", { tone: "error" }),
  });

  if (report) {
    return (
      <Panel className="p-4">
        <div className="flex items-center gap-2">
          <Tag tone={report.verified ? "positive" : "caution"}>
            {report.verified ? "Erased and verified" : "Erased, residue found"}
          </Tag>
        </div>
        <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--ink-2)]">
          {report.documents_erased} document{report.documents_erased === 1 ? "" : "s"},{" "}
          {report.files_deleted} stored file{report.files_deleted === 1 ? "" : "s"} and{" "}
          {report.answers_deleted} saved answer{report.answers_deleted === 1 ? "" : "s"} removed.
          {report.documents_kept_shared > 0 &&
            ` ${report.documents_kept_shared} document shared with another candidate was kept.`}
          {report.verified
            ? " Every table and the file store were searched afterwards; nothing identifying was found."
            : " A search afterwards still found:"}
        </p>
        {!report.verified && (
          <ul className="data mt-1.5">
            {report.residue.map((r, i) => (
              <li key={i}>
                {r.table}.{r.column} ({r.fingerprint})
              </li>
            ))}
          </ul>
        )}
        <Button className="mt-3" size="sm" onClick={() => router.push(returnTo)}>
          Done
        </Button>
      </Panel>
    );
  }

  return (
    <section>
      <h2 className="mb-2.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
        Candidate data
      </h2>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={download}>
          Export everything held
        </Button>
        {!confirming && (
          <Button size="sm" variant="danger" onClick={() => setConfirming(true)}>
            Erase candidate…
          </Button>
        )}
      </div>
      {confirming && (
        <Panel className="mt-3 border-[var(--caution)]/40 p-4">
          <p className="text-[13px] font-medium">Erase {name} everywhere?</p>
          <p className="mt-1 text-[12.5px] leading-relaxed text-[var(--ink-3)]">
            Their CV, extracted text, search index, assessments, scorecards, stage history
            and any saved answers that quote them are deleted, then every store is searched
            to prove it. This can&rsquo;t be undone. The audit log keeps only that it happened.
          </p>
          <label className="mt-3 block text-[12px] text-[var(--ink-3)]">
            Type <span className="font-medium text-[var(--ink)]">{name}</span> to confirm
            <Input value={typed} onChange={(e) => setTyped(e.target.value)} className="mt-1.5" />
          </label>
          <div className="mt-3 flex justify-end gap-2">
            <Button variant="quiet" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button
              variant="solid"
              disabled={typed.trim() !== name}
              loading={erase.isPending}
              onClick={() => erase.mutate()}
            >
              Erase permanently
            </Button>
          </div>
        </Panel>
      )}
    </section>
  );
}
