"use client";

import clsx from "clsx";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { CandidateData } from "@/components/candidate-data";
import { ContentWarning } from "@/components/content-warning";
import { useToast } from "@/components/toast";
import { Button, Empty, Panel, Spinner, Tag } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { currentClaims } from "@/lib/auth";
import {
  CAN_INTERVIEW,
  FORWARD_STAGES,
  RATING_LABEL,
  RECOMMENDATION_LABEL,
  REJECTION_LABEL,
  STAGE_LABEL,
  STAGE_SLA_DAYS,
  daysSince,
} from "@/lib/labels";
import type {
  ApplicationDetail,
  Member,
  Recommendation,
  RejectionReason,
  Rubric,
  Scorecard,
} from "@/lib/types";

const CAN_MOVE = new Set(["owner", "recruiter", "hiring_manager"]);

export default function ApplicationPage() {
  const { id } = useParams<{ id: string }>();
  const detail = useQuery({ queryKey: ["application", id], queryFn: () => api.application(id) });

  if (detail.isPending) {
    return (
      <div className="flex flex-1 items-center justify-center text-[var(--ink-3)]">
        <Spinner />
      </div>
    );
  }
  if (detail.isError) {
    const failure = detail.error as ApiError;
    return (
      <Empty
        title={failure.status === 404 ? "No such application" : "Could not load this application"}
        body={
          failure.status === 404
            ? "It may not exist, or you may not be on its panel."
            : failure.message
        }
      />
    );
  }
  return <Application detail={detail.data} />;
}

function Application({ detail }: { detail: ApplicationDetail }) {
  const claims = currentClaims();
  const mayMove = claims ? CAN_MOVE.has(claims.role) : false;
  const { application, candidate, requisition } = detail;
  const names = useMemberNames();

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-[60rem] px-7 py-8">
        <nav className="mb-3 text-[12px] text-[var(--ink-3)]">
          {mayMove ? (
            <Link href={`/hiring/${requisition.id}`} className="hover:text-[var(--ink)]">
              {requisition.title}
            </Link>
          ) : (
            <Link href="/interviews" className="hover:text-[var(--ink)]">
              Interviews
            </Link>
          )}{" "}
          / <span className="text-[var(--ink-2)]">{candidate.name}</span>
        </nav>

        <header className="mb-6 flex items-start justify-between gap-6">
          <div>
            <h1 className="font-serif text-[25px] leading-tight tracking-[-0.012em]">
              {candidate.name}
            </h1>
            <p className="mt-1 text-[13px] text-[var(--ink-3)]">
              {requisition.title}
              {candidate.email && ` · ${candidate.email}`}
            </p>
          </div>
          {detail.assessment && (
            <Link
              href={`/hiring/${requisition.id}?candidate=${application.id}`}
              className="shrink-0 text-right"
            >
              <span className="block font-serif text-[30px] leading-none">
                {Math.round(detail.assessment.score)}
              </span>
              <span className="data mt-1 block underline-offset-2 hover:underline">
                evidence →
              </span>
            </Link>
          )}
        </header>

        <div className="mb-3">
          <ContentWarning documentIds={candidate.document_ids} />
        </div>

        <StageTrack detail={detail} />

        {mayMove && <Moves detail={detail} />}

        <div className="mt-9 grid gap-9 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <Scorecards detail={detail} mayAssign={mayMove} names={names} />
          <History detail={detail} names={names} />
        </div>

        {claims?.role === "owner" && (
          <div className="mt-10 rule-t pt-6">
            <CandidateData
              candidateId={candidate.id}
              name={candidate.name}
              returnTo={`/hiring/${requisition.id}`}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function useMemberNames(): Map<string, string> {
  const members = useQuery({ queryKey: ["members"], queryFn: api.members });
  return new Map((members.data ?? []).map((m) => [m.user.id, m.user.display_name]));
}

/* The pipeline as a ruler. Where the application is, how long it has been there, and
   whether that is longer than it should be. */
function StageTrack({ detail }: { detail: ApplicationDetail }) {
  const { application } = detail;
  const rejected = application.stage === "rejected";
  const lastForward = rejected
    ? (detail.transitions.at(-1)?.from_stage ?? "applied")
    : application.stage;
  const reached = FORWARD_STAGES.indexOf(lastForward);
  const days = daysSince(application.stage_entered_at);
  const sla = STAGE_SLA_DAYS[application.stage];

  return (
    <Panel className="p-4">
      <ol className="grid grid-cols-5 gap-1.5">
        {FORWARD_STAGES.map((stage, index) => {
          const current = !rejected && stage === application.stage;
          return (
            <li key={stage}>
              <div
                className={clsx(
                  "h-1 rounded-full",
                  index < reached || current
                    ? "bg-[var(--ink)]"
                    : index === reached && rejected
                      ? "bg-[var(--caution)]"
                      : "bg-[var(--surface-sunk)]",
                )}
              />
              {/* Five labels don't fit a phone: there, only the current stage is named
                  and the bar carries position. */}
              <span
                className={clsx(
                  "mt-2 text-[12px]",
                  current
                    ? "block whitespace-nowrap font-medium text-[var(--ink)]"
                    : "hidden truncate text-[var(--ink-3)] sm:block",
                )}
              >
                {STAGE_LABEL[stage]}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="mt-3 flex flex-wrap items-center gap-2 text-[12.5px] text-[var(--ink-2)]">
        {rejected ? (
          <>
            <Tag tone="caution">Rejected</Tag>
            {detail.transitions.at(-1)?.reason &&
              REJECTION_LABEL[detail.transitions.at(-1)!.reason!]}
          </>
        ) : (
          <>
            {STAGE_LABEL[application.stage]} for {days === 0 ? "less than a day" : `${days} day${days === 1 ? "" : "s"}`}
            {sla !== undefined && (
              <span className="text-[var(--ink-3)]">· target {sla} days</span>
            )}
            {detail.overdue && <Tag tone="caution">Overdue</Tag>}
          </>
        )}
      </p>
    </Panel>
  );
}

function Moves({ detail }: { detail: ApplicationDetail }) {
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState<RejectionReason | null>(null);
  const [note, setNote] = useState("");
  const id = detail.application.id;
  const terminal = detail.application.stage === "hired" || detail.application.stage === "rejected";

  const move = useMutation({
    mutationFn: (input: Parameters<typeof api.moveApplication>[1]) =>
      api.moveApplication(id, input),
    onSuccess: (moved) => {
      queryClient.invalidateQueries({ queryKey: ["application", id] });
      queryClient.invalidateQueries({ queryKey: ["shortlist", moved.requisition_id] });
      setRejecting(false);
      notify(`Moved to ${STAGE_LABEL[moved.stage]}`);
    },
    onError: (error) => {
      const failure = error as ApiError;
      notify(failure.message || "Could not move the application", {
        tone: "error",
        detail: failure.correlationId ? `ref ${failure.correlationId}` : undefined,
      });
    },
  });

  if (terminal) return null;
  const next = detail.next_stage;
  const blocked = detail.next_blockers.length > 0;

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        {next && (
          <Button
            variant="solid"
            disabled={blocked}
            loading={move.isPending && move.variables?.to === next}
            onClick={() => move.mutate({ to: next })}
          >
            Move to {STAGE_LABEL[next]}
          </Button>
        )}
        {!rejecting && (
          <Button variant="danger" onClick={() => setRejecting(true)}>
            Reject…
          </Button>
        )}
      </div>

      {blocked && (
        <ul className="mt-2 space-y-0.5 text-[12.5px] text-[var(--ink-3)]">
          {detail.next_blockers.map((b) => (
            <li key={b}>Before {STAGE_LABEL[next!].toLowerCase()}: {b}.</li>
          ))}
        </ul>
      )}

      {rejecting && (
        <Panel className="mt-3 p-4">
          <p className="text-[13px] font-medium">Why is this application being rejected?</p>
          <p className="mt-0.5 text-[12px] text-[var(--ink-3)]">
            A reason code is required. It is what makes rejections countable, and fairness
            reporting possible.
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {(Object.keys(REJECTION_LABEL) as RejectionReason[]).map((code) => (
              <button
                key={code}
                onClick={() => setReason(code)}
                aria-pressed={reason === code}
                className={clsx(
                  "h-7 rounded-[var(--radius-xs)] border px-2.5 text-[12.5px] transition-colors",
                  reason === code
                    ? "border-[var(--ink)] bg-[var(--ink)] text-[var(--paper)]"
                    : "border-[var(--rule-strong)] text-[var(--ink-2)] hover:border-[var(--ink-4)]",
                )}
              >
                {REJECTION_LABEL[code]}
              </button>
            ))}
          </div>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            placeholder="A note for the record (optional)"
            className="mt-3 w-full resize-y rounded-[var(--radius-sm)] border border-[var(--rule-strong)] bg-[var(--surface)] px-3 py-2 text-[13px] placeholder:text-[var(--ink-3)] focus:border-[var(--ink)] focus:outline-none"
          />
          <div className="mt-2.5 flex justify-end gap-2">
            <Button variant="quiet" onClick={() => setRejecting(false)}>
              Cancel
            </Button>
            <Button
              variant="solid"
              disabled={!reason}
              loading={move.isPending && move.variables?.to === "rejected"}
              onClick={() => reason && move.mutate({ to: "rejected", reason, note })}
            >
              Reject
            </Button>
          </div>
        </Panel>
      )}
    </div>
  );
}

function Scorecards({
  detail,
  mayAssign,
  names,
}: {
  detail: ApplicationDetail;
  mayAssign: boolean;
  names: Map<string, string>;
}) {
  const { own, others, withheld, pending } = detail.scorecards;
  const terminal = detail.application.stage === "hired" || detail.application.stage === "rejected";

  return (
    <section>
      <div className="mb-2.5 flex items-baseline justify-between">
        <h2 className="text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
          Scorecards
        </h2>
        <span className="text-[12px] text-[var(--ink-3)]">
          {pending > 0 ? `${pending} still to submit` : ""}
        </span>
      </div>

      {own && !own.is_submitted && detail.rubric && (
        <ScorecardForm card={own} rubric={detail.rubric} applicationId={detail.application.id} />
      )}

      {withheld > 0 && (
        <Panel className="mb-3 border-dashed p-4 text-[12.5px] leading-relaxed text-[var(--ink-3)]">
          <span className="text-[var(--ink-2)]">
            {withheld} other scorecard{withheld === 1 ? " is" : "s are"} in.
          </span>{" "}
          You&rsquo;ll see {withheld === 1 ? "it" : "them"} once you submit yours, so your
          view is your own.
        </Panel>
      )}

      <div className="space-y-2">
        {own?.is_submitted && (
          <SubmittedCard card={own} rubric={detail.rubric} name="You" />
        )}
        {others.map((card) => (
          <SubmittedCard
            key={card.id}
            card={card}
            rubric={detail.rubric}
            name={names.get(card.interviewer_id) ?? "Interviewer"}
          />
        ))}
      </div>

      {!own && others.length === 0 && withheld === 0 && (
        <p className="text-[12.5px] text-[var(--ink-3)]">Nobody is on the panel yet.</p>
      )}

      {mayAssign && !terminal && <AddToPanel applicationId={detail.application.id} />}
    </section>
  );
}

function ScorecardForm({
  card,
  rubric,
  applicationId,
}: {
  card: Scorecard;
  rubric: Rubric;
  applicationId: string;
}) {
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const [ratings, setRatings] = useState<Record<string, number>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [summary, setSummary] = useState("");

  const submit = useMutation({
    mutationFn: () =>
      api.submitScorecard(card.id, {
        ratings: rubric.criteria
          .filter((c) => ratings[c.id])
          .map((c) => ({ criterion_id: c.id, rating: ratings[c.id]!, note: notes[c.id] ?? "" })),
        recommendation: recommendation!,
        summary,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["application", applicationId] });
      queryClient.invalidateQueries({ queryKey: ["interviews"] });
      notify("Scorecard submitted", { detail: "The panel's other scorecards are now visible." });
    },
    onError: (error) => {
      const failure = error as ApiError;
      notify(failure.message || "Could not submit", { tone: "error" });
    },
  });

  const complete = rubric.criteria.every((c) => ratings[c.id]) && recommendation && summary.trim();

  return (
    <Panel className="mb-3 p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[13.5px] font-medium">Your scorecard</h3>
        <span className="text-[12px] text-[var(--ink-3)]">Final once submitted</span>
      </div>

      <ol className="mt-3 divide-y divide-[var(--rule)]">
        {rubric.criteria.map((criterion) => (
          <li key={criterion.id} className="py-2.5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-[13px]">{criterion.label}</span>
              <div role="radiogroup" aria-label={criterion.label} className="flex gap-1">
                {[1, 2, 3, 4].map((value) => (
                  <button
                    key={value}
                    role="radio"
                    aria-checked={ratings[criterion.id] === value}
                    onClick={() => setRatings((r) => ({ ...r, [criterion.id]: value }))}
                    className={clsx(
                      "h-7 rounded-[var(--radius-xs)] border px-2 text-[12px] transition-colors",
                      ratings[criterion.id] === value
                        ? "border-[var(--ink)] bg-[var(--ink)] text-[var(--paper)]"
                        : "border-[var(--rule-strong)] text-[var(--ink-3)] hover:text-[var(--ink)]",
                    )}
                  >
                    {RATING_LABEL[value]}
                  </button>
                ))}
              </div>
            </div>
            {ratings[criterion.id] && (
              <input
                value={notes[criterion.id] ?? ""}
                onChange={(e) => setNotes((n) => ({ ...n, [criterion.id]: e.target.value }))}
                placeholder="What did you hear that supports this? (optional)"
                className="mt-2 h-8 w-full rounded-[var(--radius-sm)] border border-[var(--rule)] bg-[var(--paper)] px-2.5 text-[12.5px] placeholder:text-[var(--ink-3)] focus:border-[var(--ink)] focus:outline-none"
              />
            )}
          </li>
        ))}
      </ol>

      <div className="mt-3">
        <span className="mb-1.5 block text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
          Recommendation
        </span>
        <div role="radiogroup" className="grid grid-cols-4 gap-1">
          {(Object.keys(RECOMMENDATION_LABEL) as Recommendation[]).map((value) => (
            <button
              key={value}
              role="radio"
              aria-checked={recommendation === value}
              onClick={() => setRecommendation(value)}
              className={clsx(
                "h-8 rounded-[var(--radius-sm)] border text-[12.5px] transition-colors",
                recommendation === value
                  ? "border-[var(--ink)] bg-[var(--ink)] text-[var(--paper)]"
                  : "border-[var(--rule-strong)] text-[var(--ink-2)] hover:border-[var(--ink-4)]",
              )}
            >
              {RECOMMENDATION_LABEL[value]}
            </button>
          ))}
        </div>
      </div>

      <textarea
        value={summary}
        onChange={(e) => setSummary(e.target.value)}
        rows={3}
        placeholder="In a sentence or two: why this recommendation?"
        className="mt-3 w-full resize-y rounded-[var(--radius-sm)] border border-[var(--rule-strong)] bg-[var(--surface)] px-3 py-2 text-[13px] placeholder:text-[var(--ink-3)] focus:border-[var(--ink)] focus:outline-none"
        aria-label="Summary"
      />
      <div className="mt-2.5 flex justify-end">
        <Button variant="solid" disabled={!complete} loading={submit.isPending} onClick={() => submit.mutate()}>
          Submit scorecard
        </Button>
      </div>
    </Panel>
  );
}

function SubmittedCard({
  card,
  rubric,
  name,
}: {
  card: Scorecard;
  rubric: Rubric | null;
  name: string;
}) {
  const labels = new Map(rubric?.criteria.map((c) => [c.id, c.label]) ?? []);
  return (
    <Panel className="p-4">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] font-medium">{name}</span>
        {card.recommendation && (
          <span
            className={clsx(
              "text-[12.5px]",
              card.recommendation.endsWith("no") ? "text-[var(--caution)]" : "text-[var(--ink)]",
            )}
          >
            {RECOMMENDATION_LABEL[card.recommendation]}
          </span>
        )}
      </div>
      <p className="mt-1.5 font-serif text-[14px] leading-relaxed text-[var(--ink-2)]">{card.summary}</p>
      {card.ratings.length > 0 && (
        <ul className="mt-2.5 space-y-1">
          {card.ratings.map((r) => (
            <li key={r.criterion_id} className="flex items-baseline justify-between gap-3 text-[12px]">
              <span className="truncate text-[var(--ink-3)]">{labels.get(r.criterion_id) ?? "Criterion"}</span>
              <span className="shrink-0 text-[var(--ink-2)]">{RATING_LABEL[r.rating]}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function AddToPanel({ applicationId }: { applicationId: string }) {
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const members = useQuery({ queryKey: ["members"], queryFn: api.members });
  const [choice, setChoice] = useState("");

  const eligible = (members.data ?? []).filter(
    (m: Member) => !m.membership.revoked_at && CAN_INTERVIEW.includes(m.membership.role),
  );

  const assign = useMutation({
    mutationFn: () => api.assignInterviewer(applicationId, choice),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["application", applicationId] });
      setChoice("");
      notify("Added to the panel");
    },
    onError: (error) => notify((error as ApiError).message || "Could not add", { tone: "error" }),
  });

  return (
    <div className="mt-3 flex gap-2">
      <select
        value={choice}
        onChange={(e) => setChoice(e.target.value)}
        className="h-8 min-w-0 flex-1 rounded-[var(--radius-sm)] border border-[var(--rule-strong)] bg-[var(--surface)] px-2 text-[12.5px] focus:border-[var(--ink)] focus:outline-none"
        aria-label="Interviewer"
      >
        <option value="">Add someone to the panel…</option>
        {eligible.map((m) => (
          <option key={m.user.id} value={m.user.id}>
            {m.user.display_name} · {m.membership.role.replace("_", " ")}
          </option>
        ))}
      </select>
      <Button size="sm" className="h-8" disabled={!choice} loading={assign.isPending} onClick={() => assign.mutate()}>
        Add
      </Button>
    </div>
  );
}

function History({ detail, names }: { detail: ApplicationDetail; names: Map<string, string> }) {
  const events = [
    { at: detail.application.created_at, text: "Applied", by: null as string | null, note: "" },
    ...detail.transitions.map((t) => ({
      at: t.at,
      text:
        t.to_stage === "rejected"
          ? `Rejected — ${t.reason ? REJECTION_LABEL[t.reason] : "no reason"}`
          : `${STAGE_LABEL[t.from_stage]} → ${STAGE_LABEL[t.to_stage]}`,
      by: t.by,
      note: t.note,
    })),
  ].reverse();

  return (
    <section>
      <h2 className="mb-2.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
        History
      </h2>
      <ol className="relative space-y-3.5 border-l border-[var(--rule)] pl-4">
        {events.map((event, i) => (
          <li key={i} className="relative">
            <span className="absolute -left-[20.5px] top-[5px] size-2 rounded-full border border-[var(--rule-strong)] bg-[var(--paper)]" />
            <span className="block text-[12.5px] text-[var(--ink)]">{event.text}</span>
            <span className="data block">
              {new Date(event.at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
              {event.by && ` · ${names.get(event.by) ?? "someone"}`}
            </span>
            {event.note && (
              <span className="mt-0.5 block text-[12px] text-[var(--ink-3)]">&ldquo;{event.note}&rdquo;</span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
