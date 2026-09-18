"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { AssessmentView } from "@/components/hiring/assessment-view";
import { CandidatesPanel } from "@/components/hiring/candidates-panel";
import { RubricPanel } from "@/components/hiring/rubric-panel";
import { Empty, Spinner } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";

export default function RequisitionPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Requisition />
    </Suspense>
  );
}

function Loading() {
  return (
    <div className="flex flex-1 items-center justify-center text-[var(--ink-3)]">
      <Spinner />
    </div>
  );
}

function Requisition() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const search = useSearchParams();
  const openApplication = search.get("candidate");

  const detail = useQuery({ queryKey: ["requisition", id], queryFn: () => api.requisition(id) });
  const shortlist = useQuery({ queryKey: ["shortlist", id], queryFn: () => api.shortlist(id) });

  if (detail.isPending) return <Loading />;
  if (detail.isError) {
    const failure = detail.error as ApiError;
    return (
      <Empty
        title={failure.status === 404 ? "No such requisition" : "Could not load this requisition"}
        body={failure.status === 404 ? "It may belong to another workspace, or never existed." : failure.message}
        action={<Link href="/hiring" className="text-[13px] underline">All requisitions</Link>}
      />
    );
  }

  const { requisition, rubrics } = detail.data;
  const hasApprovedRubric = rubrics.some((r) => r.status === "approved");

  const opened = shortlist.data?.find((e) => e.application.id === openApplication);
  if (opened?.assessment) {
    return (
      <AssessmentView
        key={opened.assessment.id}
        entry={opened}
        rubrics={rubrics}
        onBack={() => router.push(`/hiring/${id}`)}
      />
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-[56rem] px-7 py-8">
        <nav className="mb-3 text-[12px] text-[var(--ink-3)]">
          <Link href="/hiring" className="hover:text-[var(--ink)]">
            Hiring
          </Link>{" "}
          / <span className="text-[var(--ink-2)]">{requisition.title}</span>
        </nav>
        <header className="mb-7">
          <h1 className="font-serif text-[25px] leading-tight tracking-[-0.012em]">{requisition.title}</h1>
          {requisition.description && (
            <p className="mt-1 max-w-[40rem] text-[13px] text-[var(--ink-3)]">{requisition.description}</p>
          )}
        </header>

        <section className="mb-9">
          <h2 className="mb-2.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
            Rubric
          </h2>
          <RubricPanel requisitionId={id} rubrics={rubrics} />
        </section>

        <CandidatesPanel
          requisitionId={id}
          hasApprovedRubric={hasApprovedRubric}
          onOpen={(applicationId) => router.push(`/hiring/${id}?candidate=${applicationId}`)}
        />
      </div>
    </div>
  );
}
