"use client";

import clsx from "clsx";
import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Button, Panel, StatusDot } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";

type Step = "queued" | "uploading" | "adding" | "assessing" | "done" | "failed";

interface Row {
  file: File;
  name: string;
  step: Step;
  error?: string;
}

const PARALLEL = 3;
const LABEL: Record<Step, string> = {
  queued: "Waiting",
  uploading: "Uploading",
  adding: "Adding candidate",
  assessing: "Queuing assessment",
  done: "Added",
  failed: "Failed",
};

/* "asha-rao_CV (2).pdf" -> "Asha Rao". A starting point the recruiter can see before
   anything happens -- names come from files, and files are named badly. */
export function nameFromFile(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, "").replace(/\(\d+\)/g, "");
  const words = stem
    .split(/[\s._-]+/)
    .filter((w) => w && !/^(cv|resume|résumé|curriculum|vitae|final|updated|\d+)$/i.test(w));
  return words.map((w) => w[0]!.toUpperCase() + w.slice(1).toLowerCase()).join(" ") || stem;
}

/* One bad PDF must not fail the batch: every file runs to its own conclusion, and the
   failures are listed with their reason rather than swallowed.

   The browser only uploads. Reading each CV and assessing it happen on the server: an
   assessment queued before its CV is read waits for it there, so once the uploads
   finish this page can be closed and the shortlist fills in on its own. */
export function BulkIntake({
  requisitionId,
  assessOnAdd,
  onClose,
}: {
  requisitionId: string;
  assessOnAdd: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [running, setRunning] = useState(false);

  function update(index: number, patch: Partial<Row>) {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function accept(files: FileList | null) {
    if (!files) return;
    setRows((current) => [
      ...current,
      ...Array.from(files).map((file) => ({ file, name: nameFromFile(file.name), step: "queued" as Step })),
    ]);
  }

  async function one(row: Row, index: number) {
    try {
      update(index, { step: "uploading" });
      const uploaded = await api.upload(row.file);
      update(index, { step: "adding" });
      const added = await api.addCandidate(requisitionId, {
        name: row.name.trim(),
        document_ids: [uploaded.document.id],
      });
      if (assessOnAdd) {
        update(index, { step: "assessing" });
        await api.assess(added.application.id);
      }
      update(index, { step: "done" });
    } catch (error) {
      update(index, { step: "failed", error: (error as ApiError).message || "failed" });
    }
  }

  async function start() {
    setRunning(true);
    const pending = rows.map((row, index) => ({ row, index })).filter(({ row }) => row.step === "queued");
    let next = 0;
    await Promise.all(
      Array.from({ length: PARALLEL }, async () => {
        while (next < pending.length) {
          const item = pending[next++]!;
          await one(item.row, item.index);
        }
      }),
    );
    setRunning(false);
    queryClient.invalidateQueries({ queryKey: ["shortlist", requisitionId] });
    queryClient.invalidateQueries({ queryKey: ["documents"] });
  }

  const done = rows.filter((r) => r.step === "done").length;
  const failed = rows.filter((r) => r.step === "failed").length;
  const queued = rows.filter((r) => r.step === "queued").length;

  return (
    <Panel className="mb-3 p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[13.5px] font-medium">Add candidates from CVs</h3>
        <span className="text-[12px] text-[var(--ink-3)]">
          One candidate per file, named from the filename.
          {assessOnAdd && " Each is assessed as soon as its CV is read."}
        </span>
      </div>

      <button
        onClick={() => input.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          accept(e.dataTransfer.files);
        }}
        disabled={running}
        className="mt-3 flex w-full flex-col items-center justify-center rounded-[var(--radius-sm)] border border-dashed border-[var(--rule-strong)] px-4 py-6 text-[12.5px] text-[var(--ink-3)] transition-colors hover:border-[var(--ink-4)] hover:text-[var(--ink-2)]"
      >
        Drop CVs here, or click to choose · PDF, DOCX, TXT
      </button>
      <input
        ref={input}
        type="file"
        multiple
        accept=".pdf,.docx,.txt,.md"
        className="hidden"
        onChange={(e) => accept(e.target.files)}
      />

      {rows.length > 0 && (
        <ul className="mt-3 max-h-72 divide-y divide-[var(--rule)] overflow-auto rounded-[var(--radius-sm)] border border-[var(--rule)]">
          {rows.map((row, index) => (
            <li key={index} className="flex items-center gap-3 px-3 py-2">
              <StatusDot
                status={row.step === "done" ? "ready" : row.step === "failed" ? "failed" : row.step === "queued" ? "pending" : "processing"}
              />
              {row.step === "queued" ? (
                <input
                  value={row.name}
                  onChange={(e) => update(index, { name: e.target.value })}
                  aria-label={`Candidate name for ${row.file.name}`}
                  className="min-w-0 flex-1 bg-transparent text-[13px] focus:outline-none"
                />
              ) : (
                <span className="min-w-0 flex-1 truncate text-[13px]">{row.name}</span>
              )}
              <span className="data hidden truncate sm:block sm:max-w-[12rem]">{row.file.name}</span>
              <span
                className={clsx(
                  "w-40 shrink-0 text-right text-[12px]",
                  row.step === "failed" ? "text-[var(--caution)]" : "text-[var(--ink-3)]",
                )}
                title={row.error}
              >
                {row.step === "failed" ? `Failed: ${row.error}` : LABEL[row.step]}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-[12px] text-[var(--ink-3)]">
          {rows.length > 0 && `${done} added${failed ? `, ${failed} failed` : ""}${queued ? `, ${queued} ready to add` : ""}`}
          {done > 0 && !running && assessOnAdd && " · assessments continue in the background; you can close this."}
        </span>
        <div className="flex gap-2">
          <Button variant="quiet" onClick={onClose} disabled={running}>
            {done > 0 && !queued ? "Done" : "Cancel"}
          </Button>
          <Button variant="solid" disabled={!queued || running} loading={running} onClick={start}>
            Add {queued || ""} candidate{queued === 1 ? "" : "s"}
          </Button>
        </div>
      </div>
    </Panel>
  );
}
