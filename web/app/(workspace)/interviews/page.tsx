"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Empty, Panel, Spinner, Tag } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { RECOMMENDATION_LABEL, STAGE_LABEL } from "@/lib/labels";

export default function InterviewsPage() {
  const interviews = useQuery({ queryKey: ["interviews"], queryFn: api.myInterviews });
  const list = interviews.data ?? [];
  const open = list.filter((i) => !i.scorecard.is_submitted);
  const done = list.filter((i) => i.scorecard.is_submitted);

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-[48rem] px-7 py-8">
        <header className="mb-6">
          <h1 className="font-serif text-[25px] leading-tight tracking-[-0.012em]">Interviews</h1>
          <p className="mt-1 text-[13px] text-[var(--ink-3)]">
            Panels you&rsquo;re on. Other interviewers&rsquo; scorecards stay hidden until you&rsquo;ve
            submitted yours.
          </p>
        </header>

        {interviews.isPending ? (
          <div className="flex justify-center py-16 text-[var(--ink-3)]">
            <Spinner />
          </div>
        ) : interviews.isError ? (
          <Empty title="Could not load your interviews" body={(interviews.error as ApiError).message} />
        ) : list.length === 0 ? (
          <Panel>
            <Empty
              title="You're not on any panels"
              body="When a recruiter adds you to a candidate's interview panel, it appears here."
            />
          </Panel>
        ) : (
          <div className="space-y-7">
            {[
              { title: "To write", items: open },
              { title: "Submitted", items: done },
            ]
              .filter((g) => g.items.length > 0)
              .map((group) => (
                <section key={group.title}>
                  <h2 className="mb-2.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
                    {group.title} · {group.items.length}
                  </h2>
                  <ul className="divide-y divide-[var(--rule)] rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--surface)]">
                    {group.items.map(({ scorecard, application, candidate, requisition }) => (
                      <li key={scorecard.id}>
                        <Link
                          href={`/applications/${application.id}`}
                          className="flex items-center justify-between gap-4 px-4 py-3 transition-colors hover:bg-[var(--surface-sunk)]"
                        >
                          <span className="min-w-0">
                            <span className="block truncate text-[14px] font-medium">{candidate.name}</span>
                            <span className="block truncate text-[12.5px] text-[var(--ink-3)]">
                              {requisition.title} · {STAGE_LABEL[application.stage]}
                            </span>
                          </span>
                          {scorecard.recommendation ? (
                            <span className="shrink-0 text-[12.5px] text-[var(--ink-2)]">
                              {RECOMMENDATION_LABEL[scorecard.recommendation]}
                            </span>
                          ) : (
                            <Tag>Draft</Tag>
                          )}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
