"use client";

import clsx from "clsx";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Empty, Panel, Spinner, Tag } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { currentClaims } from "@/lib/auth";
import type { GroupRate, RetentionRun } from "@/lib/types";

const GROUP_LABEL: Record<string, string> = {
  woman: "Women",
  man: "Men",
  non_binary: "Non-binary",
  undisclosed: "Undisclosed",
};

const pct = (value: number | null) => (value === null ? "—" : `${Math.round(value * 100)}%`);

export default function GovernancePage() {
  const owner = currentClaims()?.role === "owner";
  const report = useQuery({ queryKey: ["selection-rates"], queryFn: () => api.selectionRates() });

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-[56rem] px-7 py-8">
        <header className="mb-7">
          <h1 className="font-serif text-[25px] leading-tight tracking-[-0.012em]">Governance</h1>
          <p className="mt-1 max-w-[40rem] text-[13px] text-[var(--ink-3)]">
            Whether screening treats groups differently, and how long candidate data is kept.
            Group membership here is only ever what candidates chose to tell you. It is never
            inferred, and never shown to anyone assessing them.
          </p>
        </header>

        <section>
          <h2 className="mb-2.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
            Screening selection rates · gender
          </h2>
          {report.isPending ? (
            <Spinner />
          ) : report.isError ? (
            <Empty title="Could not load the report" body={(report.error as ApiError).message} />
          ) : report.data.groups.length === 0 ? (
            <Panel>
              <Empty
                title="No screening decisions yet"
                body="Rates appear once applications have been screened or rejected."
              />
            </Panel>
          ) : (
            <Panel>
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left text-[11.5px] text-[var(--ink-3)]">
                    {["Group", "Decided", "Advanced", "Rate", "vs. highest", ""].map((h) => (
                      <th key={h} className="px-4 py-2.5 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--rule)]">
                  {report.data.groups.map((g) => (
                    <Row key={g.group} group={g} />
                  ))}
                </tbody>
              </table>
              <p className="rule-t px-4 py-3 text-[12px] leading-relaxed text-[var(--ink-3)]">
                A group selected at under {pct(report.data.four_fifths)} of the most-selected
                group&rsquo;s rate is flagged (the four-fifths rule). That&rsquo;s a reason to look
                closer, not a finding of bias. Groups with fewer than {report.data.min_group_size}{" "}
                decisions get no ratio: at that size it would be noise that looks precise.
                {report.data.pending > 0 &&
                  ` ${report.data.pending} application${report.data.pending === 1 ? " is" : "s are"} still awaiting a decision.`}
              </p>
            </Panel>
          )}
        </section>

        {owner && <Retention />}
      </div>
    </div>
  );
}

function Row({ group }: { group: GroupRate }) {
  return (
    <tr>
      <td className="px-4 py-2.5">{GROUP_LABEL[group.group] ?? group.group}</td>
      <td className="data px-4 py-2.5">{group.decided}</td>
      <td className="data px-4 py-2.5">{group.advanced}</td>
      <td className="px-4 py-2.5">{pct(group.rate)}</td>
      <td className={clsx("px-4 py-2.5", group.adverse && "text-[var(--caution)]")}>
        {group.too_few ? <span className="text-[var(--ink-3)]">too few to tell</span> : pct(group.impact_ratio)}
      </td>
      <td className="px-4 py-2.5 text-right">{group.adverse && <Tag tone="caution">Below four-fifths</Tag>}</td>
    </tr>
  );
}

function Retention() {
  const dry = useMutation({ mutationFn: () => api.retention(true) });
  const run = useMutation({ mutationFn: () => api.retention(false) });
  const preview: RetentionRun | undefined = dry.data;

  return (
    <section className="mt-10">
      <h2 className="mb-2.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
        Retention
      </h2>
      <Panel className="p-4">
        <p className="text-[12.5px] leading-relaxed text-[var(--ink-2)]">
          Rejected candidates with no activity for longer than the retention period are erased,
          with the same verification as a manual erasure. Hired candidates are not affected.
        </p>
        <div className="mt-3 flex gap-2">
          <Button size="sm" loading={dry.isPending} onClick={() => dry.mutate()}>
            Preview who is past retention
          </Button>
          {preview && preview.expired.length > 0 && !run.data && (
            <Button size="sm" variant="danger" loading={run.isPending} onClick={() => run.mutate()}>
              Erase {preview.expired.length}
            </Button>
          )}
        </div>
        {preview && !run.data && (
          <p className="mt-3 text-[12.5px] text-[var(--ink-3)]">
            {preview.expired.length === 0
              ? `Nobody is past the ${preview.retention_days}-day retention period.`
              : `${preview.expired.length} candidate${preview.expired.length === 1 ? " is" : "s are"} past the ${preview.retention_days}-day period.`}
          </p>
        )}
        {run.data && (
          <p className="mt-3 text-[12.5px] text-[var(--ink-2)]">
            Erased {run.data.erased.length};{" "}
            {run.data.erased.every((r) => r.verified)
              ? "every erasure verified."
              : `${run.data.erased.filter((r) => !r.verified).length} left residue. See the audit log.`}
          </p>
        )}
      </Panel>
    </section>
  );
}
