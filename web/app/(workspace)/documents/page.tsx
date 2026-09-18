"use client";

import clsx from "clsx";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { useToast } from "@/components/toast";
import { Button, Empty, Spinner, StatusDot, Tag } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import type { DocumentSummary } from "@/lib/types";

const ACCEPTED = ".pdf,.docx,.txt,.md";

export default function LibraryPage() {
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents });

  const upload = useMutation({
    mutationFn: (file: File) => api.upload(file),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      notify(
        result.was_duplicate
          ? `${result.document.title} was already here`
          : `${result.document.title} uploaded`,
        {
          detail: result.was_duplicate
            ? "Identical content — the existing copy was kept and nothing was reprocessed."
            : "Processing has started.",
        },
      );
    },
    onError: (error) => {
      const failure = error as ApiError;
      notify(failure.message || "Upload failed", {
        tone: "error",
        detail: failure.correlationId ? `ref ${failure.correlationId}` : undefined,
      });
    },
  });

  function accept(files: FileList | null) {
    if (!files) return;
    for (const file of Array.from(files)) upload.mutate(file);
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-[56rem] px-7 py-8">
        <header className="mb-6">
          <h1 className="font-serif text-[25px] leading-tight tracking-[-0.012em]">Library</h1>
          <p className="mt-1 text-[13px] text-[var(--ink-3)]">
            Everything here is searchable. Re-uploading identical content is free — it
            is recognised and skipped.
          </p>
        </header>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            accept(e.dataTransfer.files);
          }}
          className={clsx(
            "rounded-[var(--radius-md)] border border-dashed p-7 text-center",
            "transition-colors duration-150",
            dragging
              ? "border-[var(--evidence-edge)] bg-[var(--evidence-fill)]"
              : "border-[var(--rule-strong)] bg-[var(--surface)]",
          )}
        >
          <input
            ref={fileRef}
            type="file"
            accept={ACCEPTED}
            multiple
            hidden
            onChange={(e) => {
              accept(e.target.files);
              e.target.value = "";
            }}
          />
          <p className="text-[13.5px] text-[var(--ink-2)]">
            Drop files here, or{" "}
            <button
              onClick={() => fileRef.current?.click()}
              className="font-medium text-[var(--ink)] underline decoration-[var(--rule-strong)] underline-offset-[3px] transition-colors hover:decoration-[var(--ink)]"
            >
              browse
            </button>
          </p>
          <p className="data mt-1.5">PDF · DOCX · TXT · MD — up to 50 MB</p>
          {upload.isPending && (
            <p className="mt-3 inline-flex items-center gap-2 text-[12.5px] text-[var(--ink-3)]">
              <Spinner /> Uploading…
            </p>
          )}
        </div>

        <div className="mt-7">
          {documents.isLoading && (
            <p className="flex items-center gap-2 py-8 text-[13px] text-[var(--ink-3)]">
              <Spinner /> Loading library…
            </p>
          )}

          {documents.data?.length === 0 && (
            <Empty
              title="Nothing here yet"
              body="Upload a document and it will be parsed, split into passages and indexed. Then you can ask questions of it."
              action={
                <Button variant="solid" onClick={() => fileRef.current?.click()}>
                  Upload a document
                </Button>
              }
            />
          )}

          {documents.data && documents.data.length > 0 && (
            <ul className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--rule)]">
              {documents.data.map((document, index) => (
                <DocumentRow key={document.id} document={document} first={index === 0} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function DocumentRow({
  document,
  first,
}: {
  document: DocumentSummary;
  first: boolean;
}) {
  const router = useRouter();

  const status = useQuery({
    queryKey: ["document-status", document.id],
    queryFn: () => api.documentStatus(document.id),
    // Poll only while work is outstanding. A ready document is not going to
    // change, and a failed one needs a person, not another request.
    refetchInterval: (query) => {
      const state = query.state.data?.status;
      return state === "pending" || state === "processing" ? 1500 : false;
    },
  });

  const state = status.data?.status ?? "pending";

  return (
    <li className={clsx(!first && "rule-t")}>
      <button
        onClick={() => router.push("/ask")}
        className="flex w-full items-center gap-3 bg-[var(--surface)] px-4 py-3 text-left transition-colors duration-150 hover:bg-[var(--surface-sunk)]"
      >
        <StatusDot status={state} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13.5px] text-[var(--ink)]">
            {document.title}
          </span>
          <span className="data mt-0.5 block">
            {state === "ready" && status.data
              ? `${status.data.page_count ?? 1} page${status.data.page_count === 1 ? "" : "s"} · ${status.data.char_count?.toLocaleString() ?? 0} chars`
              : state === "failed"
                ? (status.data?.failure_reason ?? "Processing failed")
                : state === "processing"
                  ? "Parsing, chunking, embedding…"
                  : "Queued"}
          </span>
        </span>
        {state === "failed" && <Tag tone="caution">Failed</Tag>}
        {state === "ready" && (
          <span className="text-[11.5px] text-[var(--ink-4)]">
            {new Date(document.created_at).toLocaleDateString(undefined, {
              day: "numeric",
              month: "short",
            })}
          </span>
        )}
      </button>
    </li>
  );
}
