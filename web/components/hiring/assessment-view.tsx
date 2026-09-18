"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";
import { ContentWarning } from "@/components/content-warning";
import { DocumentViewer } from "@/components/document-viewer";
import { Button, Empty, Tag } from "@/components/ui/primitives";
import type { Citation, Criterion, Rubric, ShortlistEntry, Verdict } from "@/lib/types";
import { FLAG_LABEL } from "./candidates-panel";
import { KIND_LABEL } from "./rubric-panel";

const VERDICT: Record<Verdict, { label: string; steps: number }> = {
  strong: { label: "Strong", steps: 3 },
  adequate: { label: "Adequate", steps: 2 },
  weak: { label: "Weak", steps: 1 },
  absent: { label: "Not found", steps: 0 },
};

const OFFLINE_JUDGE = "overlap-judge-v1";

/* Verdicts, not a single number, are the product. The score sits in the header as a
   summary; the page is the list of criteria, each one answerable with "show me". */
export function AssessmentView({
  entry,
  rubrics,
  onBack,
}: {
  entry: ShortlistEntry;
  rubrics: Rubric[];
  onBack: () => void;
}) {
  const assessment = entry.assessment!;
  const rubric = rubrics.find((r) => r.id === assessment.rubric_id);
  const criteria = useMemo(
    () => new Map<string, Criterion>(rubric?.criteria.map((c) => [c.id, c]) ?? []),
    [rubric],
  );

  // Open on the first criterion that has evidence: an empty viewer is a worse first
  // impression than the candidate's strongest passage.
  const first = assessment.judgments.find((j) => j.evidence.length > 0) ?? assessment.judgments[0];
  const [activeCriterion, setActiveCriterion] = useState<string | null>(first?.criterion_id ?? null);
  const [activeLabel, setActiveLabel] = useState<string | null>(first?.evidence[0]?.label ?? null);

  const active = assessment.judgments.find((j) => j.criterion_id === activeCriterion) ?? null;
  const citations: Citation[] = (active?.evidence ?? []).map((e) => ({ ...e }));
  const openDocument =
    citations.find((c) => c.label === activeLabel)?.document_id ??
    citations[0]?.document_id ??
    entry.candidate.document_ids[0] ??
    null;
  const offline = assessment.model_version === OFFLINE_JUDGE;
  const outdated = rubric?.status === "superseded";

  return (
    <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      <section className="rule-r min-h-0 overflow-auto">
        <div className="px-7 py-6">
          <Button variant="quiet" size="sm" onClick={onBack} className="-ml-2.5 mb-3">
            ← All candidates
          </Button>

          <header className="flex items-start justify-between gap-6">
            <div className="min-w-0">
              <h2 className="truncate font-serif text-[23px] leading-tight tracking-[-0.012em]">
                {entry.candidate.name}
              </h2>
              <p className="mt-1 text-[12.5px] text-[var(--ink-3)]">
                Against rubric v{assessment.rubric_version}
                {outdated && " (since revised — re-assess to use the current one)"} ·{" "}
                {new Date(assessment.created_at).toLocaleString(undefined, {
                  day: "numeric",
                  month: "short",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </p>
            </div>
            <div className="shrink-0 text-right">
              <div className="font-serif text-[34px] leading-none">{Math.round(assessment.score)}</div>
              <div className="data mt-1">of 100</div>
            </div>
          </header>

          <div className="mt-4">
            <ContentWarning documentIds={entry.candidate.document_ids} />
          </div>

          {assessment.flags.length > 0 && (
            <div className="mt-4 rounded-[var(--radius-md)] border border-[var(--caution)]/35 p-3.5">
              <div className="flex flex-wrap gap-1">
                {assessment.flags.map((flag) => (
                  <Tag key={flag} tone="caution">
                    {FLAG_LABEL[flag]}
                  </Tag>
                ))}
              </div>
              <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--ink-2)]">
                Nobody is rejected automatically. &ldquo;Not found in these documents&rdquo; and
                &ldquo;does not have it&rdquo; are different claims — a person should read the CV
                before deciding.
              </p>
            </div>
          )}

          {offline && (
            <aside className="mt-4 rounded-[var(--radius-md)] border border-[var(--rule-strong)] bg-[var(--surface-sunk)] p-3.5 text-[12.5px] leading-relaxed text-[var(--ink-3)]">
              <Tag>Baseline judge</Tag>{" "}
              <span className="text-[var(--ink-2)]">Verdicts come from term overlap, not a model.</span>{" "}
              It cannot read negation — &ldquo;never used Kubernetes&rdquo; counts as a mention.
              Set <code className="data">GEMINI_API_KEY</code> or{" "}
              <code className="data">ANTHROPIC_API_KEY</code> for judged verdicts.
            </aside>
          )}

          <ol className="mt-6 space-y-2">
            {assessment.judgments.map((judgment) => {
              const criterion = criteria.get(judgment.criterion_id);
              const selected = judgment.criterion_id === activeCriterion;
              const verdict = VERDICT[judgment.verdict];
              return (
                <li key={judgment.criterion_id}>
                  <div
                    className={clsx(
                      "rounded-[var(--radius-md)] border p-3.5 transition-colors",
                      selected
                        ? "border-[var(--rule-strong)] bg-[var(--surface)] shadow-[var(--shadow-pop)]"
                        : "border-[var(--rule)] bg-[var(--surface)]/60 hover:border-[var(--rule-strong)]",
                    )}
                  >
                    <button
                      className="flex w-full items-start justify-between gap-4 text-left"
                      onClick={() => {
                        setActiveCriterion(judgment.criterion_id);
                        setActiveLabel(judgment.evidence[0]?.label ?? null);
                      }}
                      aria-expanded={selected}
                    >
                      <span className="min-w-0">
                        <span className="block text-[13.5px] font-medium">
                          {criterion?.label ?? "Criterion"}
                        </span>
                        {criterion && (
                          <span className="text-[11.5px] text-[var(--ink-3)]">
                            {KIND_LABEL[criterion.kind]} · weight {criterion.weight}
                          </span>
                        )}
                      </span>
                      <VerdictMark verdict={judgment.verdict} label={verdict.label} steps={verdict.steps} />
                    </button>

                    {selected && (
                      <div className="mt-2.5">
                        <p className="font-serif text-[14px] leading-relaxed text-[var(--ink-2)]">
                          {judgment.rationale}
                        </p>
                        {judgment.evidence.length > 0 ? (
                          <ul className="mt-2.5 space-y-1.5">
                            {judgment.evidence.map((evidence) => (
                              <li key={evidence.label}>
                                <button
                                  onClick={() => setActiveLabel(evidence.label)}
                                  onMouseEnter={() => setActiveLabel(evidence.label)}
                                  className={clsx(
                                    "flex w-full gap-2.5 rounded-[var(--radius-sm)] border p-2.5 text-left transition-colors",
                                    activeLabel === evidence.label
                                      ? "border-[var(--evidence-edge)] bg-[var(--evidence-fill)]"
                                      : "border-[var(--rule)] hover:border-[var(--rule-strong)]",
                                  )}
                                >
                                  <span className="mt-[1px] shrink-0 text-[10.5px] font-semibold text-[var(--evidence)]">
                                    {evidence.label}
                                  </span>
                                  <span className="min-w-0">
                                    <span className="line-clamp-2 font-serif text-[13.5px] leading-snug text-[var(--ink-2)]">
                                      {evidence.quoted_text}
                                    </span>
                                    <span className="data mt-1 block">
                                      {evidence.document_title} · p{evidence.page + 1}
                                    </span>
                                  </span>
                                </button>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className="mt-2 text-[12px] text-[var(--ink-3)]">
                            Nothing in {entry.candidate.name}&rsquo;s documents was cited for this.
                          </p>
                        )}
                        <p className="data mt-2.5">confidence {judgment.confidence.toFixed(2)}</p>
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>

          <footer className="rule-t mt-8 space-y-0.5 pt-3.5">
            <p className="data">judge {assessment.model_version} · prompt {assessment.prompt_version}</p>
            <p className="data">retrieval {assessment.retrieval_config_hash.slice(0, 12)}</p>
            <p className="data">
              {assessment.redaction_profile === "none"
                ? "judge saw identifying details"
                : `judged blind (${assessment.redaction_profile}): no name, contact, institution or demographics`}
            </p>
          </footer>
        </div>
      </section>

      <section className="hidden min-h-0 lg:block">
        <div className="rule-b flex h-10 items-center justify-between bg-[var(--surface)] px-4">
          <span className="truncate text-[12.5px] text-[var(--ink-2)]">
            {citations.find((c) => c.document_id === openDocument)?.document_title ?? "Candidate document"}
          </span>
          {citations.length > 0 && <Tag tone="evidence">{citations.length} highlighted</Tag>}
        </div>
        <div className="h-[calc(100%-2.5rem)]">
          {openDocument ? (
            <DocumentViewer
              documentId={openDocument}
              citations={citations}
              activeLabel={activeLabel}
              onActivate={setActiveLabel}
            />
          ) : (
            <Empty title="No document" body="This candidate has no documents attached." />
          )}
        </div>
      </section>
    </div>
  );
}

/* Three ticks, filled by strength. Ink only: colour is reserved for evidence. */
function VerdictMark({ verdict, label, steps }: { verdict: Verdict; label: string; steps: number }) {
  return (
    <span className="flex shrink-0 items-center gap-2">
      <span
        className={clsx(
          "text-[12px]",
          verdict === "absent" ? "text-[var(--ink-3)]" : "text-[var(--ink)]",
        )}
      >
        {label}
      </span>
      <span className="flex gap-[3px]" aria-hidden>
        {[1, 2, 3].map((step) => (
          <span
            key={step}
            className={clsx(
              "h-3 w-1 rounded-[1px]",
              step <= steps ? "bg-[var(--ink)]" : "bg-[var(--rule-strong)]",
            )}
          />
        ))}
      </span>
    </span>
  );
}
