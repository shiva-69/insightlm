"use client";

import { useQueries } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import type { ContentFinding, FindingKind } from "@/lib/types";
import { Tag } from "./ui/primitives";

const HOW: Record<FindingKind, string> = {
  invisible_text: "transparent",
  low_contrast_text: "same colour as its background",
  micro_text: "too small to read",
  off_page_text: "placed outside the page",
  metadata_instruction: "in the file's metadata",
  instruction_like_text: "visible, addressed to an AI",
};

/* Shown wherever a person is about to trust a model's reading of these documents.
   The point is to show what was hidden from them, not merely that something was. */
export function ContentWarning({ documentIds }: { documentIds: string[] }) {
  const [open, setOpen] = useState(false);
  const results = useQueries({
    queries: documentIds.map((id) => ({
      queryKey: ["findings", id],
      queryFn: () => api.findings(id),
      staleTime: 60_000,
    })),
  });
  const statuses = useQueries({
    queries: documentIds.map((id) => ({
      queryKey: ["document-status", id],
      queryFn: () => api.documentStatus(id),
      staleTime: 60_000,
    })),
  });
  const findings: ContentFinding[] = results.flatMap((r) => r.data ?? []);
  const scanned = statuses.some((s) => s.data?.read_by_ocr);
  if (findings.length === 0) return scanned ? <ScanNote /> : null;

  const hidden = findings.filter((f) => f.excluded);
  const addressed = findings.filter((f) => !f.excluded && f.instruction_like);

  return (
    <aside className="rounded-[var(--radius-md)] border border-[var(--caution)]/40 bg-[var(--surface)] p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Tag tone="caution">{hidden.length > 0 ? "Hidden text found" : "Text addressed to AI"}</Tag>
          <span className="text-[12.5px] text-[var(--ink-2)]">
            {hidden.length > 0 &&
              `${hidden.length} passage${hidden.length === 1 ? "" : "s"} a reader can't see ${
                hidden.length === 1 ? "was" : "were"
              } kept out of the assessment.`}
            {hidden.length > 0 && addressed.length > 0 && " "}
            {addressed.length > 0 &&
              `${addressed.length} visible line${addressed.length === 1 ? "" : "s"} appear${
                addressed.length === 1 ? "s" : ""
              } to address an AI.`}
          </span>
        </div>
        <button
          onClick={() => setOpen((v) => !v)}
          className="text-[12px] text-[var(--ink-3)] underline-offset-2 hover:text-[var(--ink)] hover:underline"
          aria-expanded={open}
        >
          {open ? "Hide" : "Show what was found"}
        </button>
      </div>
      {open && (
        <ul className="mt-3 space-y-2">
          {findings.map((finding, i) => (
            <li key={i} className="rounded-[var(--radius-sm)] bg-[var(--surface-sunk)] px-3 py-2">
              <span className="block font-serif text-[13.5px] leading-snug text-[var(--ink-2)]">
                &ldquo;{finding.text}&rdquo;
              </span>
              <span className="data mt-1 block">
                {HOW[finding.kind]}
                {finding.page !== null && ` · p${finding.page + 1}`}
                {finding.excluded ? " · not used" : " · still in the document"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {scanned && <ScanNote inline />}
      <p className="mt-2 text-[12px] leading-relaxed text-[var(--ink-3)]">
        This isn&rsquo;t proof of bad intent: templates and copy-paste leave odd text too.
        It&rsquo;s a reason to read the CV yourself.
      </p>
    </aside>
  );
}

/* A scan's text came from recognition, not the file. Usually right, sometimes not:
   a misread digit or name is possible, so quotes are worth checking on the page. */
function ScanNote({ inline = false }: { inline?: boolean }) {
  const body = (
    <div className="flex flex-wrap items-center gap-2">
      <Tag>Scanned CV</Tag>
      <span className="text-[12.5px] text-[var(--ink-2)]">
        Its text was read by OCR. Check quoted wording against the highlighted page.
      </span>
    </div>
  );
  if (inline) return <div className="mt-3">{body}</div>;
  return (
    <aside className="rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--surface)] p-3.5">
      {body}
    </aside>
  );
}
