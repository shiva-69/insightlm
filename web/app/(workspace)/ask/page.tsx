"use client";

import clsx from "clsx";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { AnswerPanel } from "@/components/answer-panel";
import { DocumentViewer } from "@/components/document-viewer";
import { useToast } from "@/components/toast";
import { Button, Kbd, Tag } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import type { Answer, Citation, Claim } from "@/lib/types";

export default function AskPage() {
  const { notify } = useToast();
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const [draft, setDraft] = useState("");
  const [asked, setAsked] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  // While the answer streams: the passages it may cite, the claims validated so far,
  // and why earlier claims were withdrawn if a corrected attempt is under way.
  const [passages, setPassages] = useState<Citation[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [scope, setScope] = useState<string[]>([]);
  const [activeLabel, setActiveLabel] = useState<string | null>(null);
  const [openDocument, setOpenDocument] = useState<string | null>(null);

  // What the reader sees: the finished answer, or the part of it validated so far.
  const streaming = isStreaming(answer, claims, notice);
  const shown: Answer | null = answer ?? (streaming ? partialAnswer(claims, passages) : null);

  const documents = useQuery({ queryKey: ["documents"], queryFn: api.documents });
  const ready = documents.data ?? [];

  const ask = useMutation({
    mutationFn: (question: string) =>
      api.askStream(
        { question, top_k: 8, document_ids: scope.length ? scope : undefined },
        {
          onEvidence: setPassages,
          onClaim: (claim) => {
            setNotice(null);
            setClaims((current) => [...current, claim]);
          },
          onWithdrawn: (reason) => {
            setClaims([]);
            setNotice(`Rewriting: ${reason}.`);
          },
        },
      ),
    onMutate: (question) => {
      setAsked(question);
      setAnswer(null);
      setPassages([]);
      setClaims([]);
      setNotice(null);
      setActiveLabel(null);
    },
    onSuccess: (result) => {
      setAnswer(result);
      // Open whichever document the evidence actually came from, so the reader
      // does not have to go and find it.
      const first = result.citations[0];
      if (first) {
        setOpenDocument(first.document_id);
        setActiveLabel(first.label);
      }
    },
    onError: (error) => {
      const failure = error as ApiError;
      notify(failure.message || "The question could not be answered", {
        tone: "error",
        detail: failure.correlationId ? `ref ${failure.correlationId}` : undefined,
      });
    },
  });

  // ⌘K from anywhere puts the cursor in the question box.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function submit() {
    const question = draft.trim();
    if (!question || ask.isPending) return;
    ask.mutate(question);
  }

  const scoped = scope.length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="rule-b shrink-0 bg-[var(--surface)] px-5 py-3.5">
        <div className="mx-auto flex max-w-[110rem] flex-col gap-2.5">
          <div className="flex items-end gap-2.5">
            <div className="relative flex-1">
              <textarea
                ref={inputRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    submit();
                  }
                }}
                rows={1}
                placeholder="Ask anything grounded in these documents…"
                className={clsx(
                  "block w-full resize-none rounded-[var(--radius-sm)] border border-[var(--rule-strong)]",
                  "bg-[var(--surface)] py-2 pl-3 pr-24 text-[14px] leading-6 text-[var(--ink)]",
                  "placeholder:text-[var(--ink-3)]",
                  "transition-colors duration-150 hover:border-[var(--ink-4)]",
                  "focus:border-[var(--ink)] focus:outline-none",
                )}
              />
              <span className="pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 items-center gap-1 sm:flex">
                <Kbd>⌘</Kbd>
                <Kbd>↵</Kbd>
              </span>
            </div>
            <Button
              variant="solid"
              onClick={submit}
              loading={ask.isPending}
              disabled={!draft.trim()}
            >
              Ask
            </Button>
          </div>

          {documents.isError && (
            <p className="text-[12px] text-[var(--ink-3)]" role="status">
              Couldn&rsquo;t load your document list, so questions search everything.{" "}
              <button className="underline" onClick={() => documents.refetch()}>
                Try again
              </button>
            </p>
          )}

          {ready.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-0.5 text-[11.5px] text-[var(--ink-3)]">Search</span>
              <ScopeChip
                label={`All ${ready.length} document${ready.length === 1 ? "" : "s"}`}
                active={!scoped}
                onClick={() => setScope([])}
              />
              {ready.map((document) => (
                <ScopeChip
                  key={document.id}
                  label={document.title}
                  active={scope.includes(document.id)}
                  onClick={() =>
                    setScope((current) =>
                      current.includes(document.id)
                        ? current.filter((id) => id !== document.id)
                        : [...current, document.id],
                    )
                  }
                />
              ))}
            </div>
          )}
        </div>
      </header>

      <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <section className="rule-r min-h-0 overflow-auto">
          <AnswerPanel
            answer={shown}
            pending={ask.isPending && !streaming}
            streaming={ask.isPending && streaming}
            notice={notice}
            question={asked}
            activeLabel={activeLabel}
            onActivate={setActiveLabel}
          />
        </section>

        <section className="hidden min-h-0 lg:block">
          <div className="rule-b flex h-10 shrink-0 items-center justify-between bg-[var(--surface)] px-4">
            <span className="truncate text-[12.5px] text-[var(--ink-2)]">
              {openDocument
                ? (ready.find((d) => d.id === openDocument)?.title ?? "Document")
                : "Source"}
            </span>
            {shown && shown.citations.length > 0 && (
              <Tag tone="evidence">
                {shown.citations.length} highlighted
              </Tag>
            )}
          </div>
          <div className="h-[calc(100%-2.5rem)]">
            <DocumentViewer
              documentId={openDocument}
              citations={shown?.citations ?? []}
              activeLabel={activeLabel}
              onActivate={setActiveLabel}
            />
          </div>
        </section>
      </div>
    </div>
  );
}

function ScopeChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "max-w-[16rem] truncate rounded-[var(--radius-xs)] border px-2 py-[3px]",
        "text-[11.5px] transition-colors duration-150",
        active
          ? "border-[var(--ink)] bg-[var(--ink)] text-[var(--paper)]"
          : "border-[var(--rule)] text-[var(--ink-3)] hover:border-[var(--rule-strong)] hover:text-[var(--ink-2)]",
      )}
    >
      {label}
    </button>
  );
}

/* Streaming has visible content once the first claim arrives, or while a withdrawn
   attempt is being rewritten. Before that, the "thinking" state shows instead. */
function isStreaming(answer: Answer | null, claims: Claim[], notice: string | null): boolean {
  return answer === null && (claims.length > 0 || notice !== null);
}

/* The answer so far, shaped like a finished one so the same panel renders it. Only
   passages a shown claim cites are listed as evidence. */
function partialAnswer(claims: Claim[], passages: Citation[]): Answer {
  const cited = new Set(claims.flatMap((c) => c.citations));
  return {
    outcome: "answered",
    text: claims.map((c) => c.text).join(" "),
    claims,
    citations: passages.filter((p) => cited.has(p.label)),
    refusal_reason: null,
    strategy: "",
    retrieval_config_hash: "",
    model_version: "",
    prompt_version: "",
    embedding_model: "",
    latency_ms: 0,
    input_tokens: 0,
    output_tokens: 0,
  };
}
