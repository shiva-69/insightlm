"use client";

import clsx from "clsx";
import { useState } from "react";
import type { Answer, Citation } from "@/lib/types";
import { Empty, Spinner, Tag } from "./ui/primitives";

interface Props {
  answer: Answer | null;
  pending: boolean;
  question: string;
  activeLabel: string | null;
  onActivate: (label: string | null) => void;
}

export function AnswerPanel({ answer, pending, question, activeLabel, onActivate }: Props) {
  const [showProvenance, setShowProvenance] = useState(false);

  if (pending) return <Thinking question={question} />;

  if (!answer) {
    return (
      <Empty
        title="Ask something"
        body="Answers come back split into claims, each carrying the passage it was drawn from. Click a citation to see it in the document."
      />
    );
  }

  const byLabel = new Map(answer.citations.map((c) => [c.label, c]));
  const grounded = answer.outcome === "answered";

  return (
    <div className="mx-auto max-w-[44rem] px-7 py-7">
      <p className="mb-5 font-serif text-[15px] italic leading-snug text-[var(--ink-3)]">
        {question}
      </p>

      {grounded ? (
        <div className="prose-answer space-y-[0.9em]">
          {answer.claims.map((claim, index) => (
            <p key={index}>
              {claim.text}
              {claim.citations.map((label) => (
                <CitationChip
                  key={label}
                  label={label}
                  citation={byLabel.get(label)}
                  active={activeLabel === label}
                  onActivate={onActivate}
                />
              ))}
            </p>
          ))}
        </div>
      ) : (
        <Unsupported answer={answer} />
      )}

      {answer.citations.length > 0 && (
        <section className="mt-9">
          <h3 className="mb-2.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
            Evidence
          </h3>
          <ul className="space-y-1.5">
            {answer.citations.map((citation) => (
              <EvidenceRow
                key={citation.label}
                citation={citation}
                active={activeLabel === citation.label}
                onActivate={onActivate}
              />
            ))}
          </ul>
        </section>
      )}

      <footer className="rule-t mt-9 pt-4">
        <button
          onClick={() => setShowProvenance((v) => !v)}
          className="flex items-center gap-1.5 text-[12px] text-[var(--ink-3)] transition-colors hover:text-[var(--ink-2)]"
          aria-expanded={showProvenance}
        >
          <svg
            viewBox="0 0 12 12"
            className={clsx(
              "size-3 transition-transform duration-150",
              showProvenance && "rotate-90",
            )}
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            aria-hidden
          >
            <path d="m4.5 2.5 3.5 3.5-3.5 3.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Provenance
          <span className="data ml-1">
            {answer.latency_ms} ms
            {answer.output_tokens > 0 &&
              ` · ${answer.input_tokens + answer.output_tokens} tok`}
          </span>
        </button>

        {showProvenance && (
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-5 gap-y-1.5">
            {[
              ["model", answer.model_version],
              ["prompt", answer.prompt_version],
              ["embedder", answer.embedding_model],
              ["strategy", answer.strategy],
              ["retrieval config", answer.retrieval_config_hash.slice(0, 24)],
              ["tokens in / out", `${answer.input_tokens} / ${answer.output_tokens}`],
            ].map(([term, value]) => (
              <div key={term} className="contents">
                <dt className="text-[11.5px] text-[var(--ink-4)]">{term}</dt>
                <dd className="data truncate text-[var(--ink-2)]">{value}</dd>
              </div>
            ))}
          </dl>
        )}
      </footer>
    </div>
  );
}

function CitationChip({
  label,
  citation,
  active,
  onActivate,
}: {
  label: string;
  citation: Citation | undefined;
  active: boolean;
  onActivate: (label: string | null) => void;
}) {
  return (
    <button
      onClick={() => onActivate(label)}
      onMouseEnter={() => onActivate(label)}
      title={citation ? `${citation.document_title} · page ${citation.page + 1}` : label}
      className={clsx(
        "mx-[3px] inline-flex h-[17px] translate-y-[-1px] items-center rounded-[3px] border px-[5px] align-middle",
        "font-sans text-[10.5px] font-semibold leading-none tracking-[0.01em]",
        "transition-colors duration-150",
        active
          ? "border-[var(--evidence-edge)] bg-[var(--evidence-fill-active)] text-[var(--evidence)]"
          : "border-[var(--evidence-edge)]/45 bg-[var(--evidence-fill)] text-[var(--evidence)] hover:bg-[var(--evidence-fill-active)]",
      )}
    >
      {label}
    </button>
  );
}

function EvidenceRow({
  citation,
  active,
  onActivate,
}: {
  citation: Citation;
  active: boolean;
  onActivate: (label: string | null) => void;
}) {
  return (
    <li>
      <button
        onClick={() => onActivate(citation.label)}
        onMouseEnter={() => onActivate(citation.label)}
        className={clsx(
          "flex w-full gap-2.5 rounded-[var(--radius-sm)] border p-2.5 text-left",
          "transition-colors duration-150",
          active
            ? "border-[var(--evidence-edge)] bg-[var(--evidence-fill)]"
            : "border-[var(--rule)] bg-[var(--surface)] hover:border-[var(--rule-strong)]",
        )}
      >
        <span className="mt-[1px] shrink-0 font-sans text-[10.5px] font-semibold text-[var(--evidence)]">
          {citation.label}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-serif text-[13.5px] leading-snug text-[var(--ink-2)]">
            {citation.quoted_text}
          </span>
          <span className="data mt-1 block">
            {citation.document_title} · p{citation.page + 1} · chars {citation.char_start}–
            {citation.char_end}
          </span>
        </span>
      </button>
    </li>
  );
}

/* Refusal is a success condition for this system, so it is presented as a result
   rather than as an error. The tone is informative, not apologetic. */
function Unsupported({ answer }: { answer: Answer }) {
  const withheld = answer.outcome === "validation_failed";
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--rule-strong)] bg-[var(--surface)] p-5">
      <Tag tone="caution">
        {withheld ? "Answer withheld" : "No supporting evidence"}
      </Tag>
      <p className="prose-answer mt-3 text-[15px]">
        {answer.refusal_reason ??
          "The documents in this workspace do not contain an answer to that question."}
      </p>
      <p className="mt-3 text-[12.5px] leading-relaxed text-[var(--ink-3)]">
        {withheld
          ? "A draft was produced but could not be tied back to the retrieved passages, so it was not shown. The passages are below — read them directly."
          : "Nothing was invented to fill the gap. Try rephrasing, or upload a document that covers it."}
      </p>
    </div>
  );
}

function Thinking({ question }: { question: string }) {
  return (
    <div className="mx-auto max-w-[44rem] px-7 py-7">
      <p className="mb-6 font-serif text-[15px] italic leading-snug text-[var(--ink-3)]">
        {question}
      </p>
      <div className="flex items-center gap-2 text-[var(--ink-3)]">
        <Spinner />
        <span className="text-[13px]">Retrieving passages…</span>
      </div>
      {/* Skeleton lines matched to the answer's measure, so the layout does not
          jump when the real text arrives. */}
      <div className="mt-6 space-y-3" aria-hidden>
        {[100, 94, 72].map((width, index) => (
          <div
            key={index}
            className="h-[13px] animate-pulse rounded-[3px] bg-[var(--rule)]"
            style={{ width: `${width}%`, animationDelay: `${index * 120}ms` }}
          />
        ))}
      </div>
    </div>
  );
}
