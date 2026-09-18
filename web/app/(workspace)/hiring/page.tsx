"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useToast } from "@/components/toast";
import { Button, Empty, Field, Input, Panel, Spinner } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";

export default function HiringPage() {
  const router = useRouter();
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const [opening, setOpening] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [blind, setBlind] = useState(true);

  const requisitions = useQuery({ queryKey: ["requisitions"], queryFn: api.requisitions });

  const create = useMutation({
    mutationFn: () =>
      api.createRequisition({
        title: title.trim(),
        description: description.trim(),
        blind_screening: blind,
      }),
    onSuccess: (requisition) => {
      queryClient.invalidateQueries({ queryKey: ["requisitions"] });
      router.push(`/hiring/${requisition.id}`);
    },
    onError: (error) => {
      const failure = error as ApiError;
      notify(failure.message || "Could not open the requisition", {
        tone: "error",
        detail: failure.correlationId ? `ref ${failure.correlationId}` : undefined,
      });
    },
  });

  const list = requisitions.data ?? [];

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-[56rem] px-7 py-8">
        <header className="mb-6 flex items-end justify-between gap-4">
          <div>
            <h1 className="font-serif text-[25px] leading-tight tracking-[-0.012em]">Hiring</h1>
            <p className="mt-1 max-w-[38rem] text-[13px] text-[var(--ink-3)]">
              Agree what good looks like before anyone is assessed. Every score is built
              from verdicts that quote the candidate&rsquo;s own documents.
            </p>
          </div>
          {!opening && list.length > 0 && (
            <Button variant="solid" onClick={() => setOpening(true)}>
              Open a requisition
            </Button>
          )}
        </header>

        {(opening || (requisitions.isSuccess && list.length === 0)) && (
          <Panel className="mb-7 p-5">
            <h2 className="text-[13.5px] font-medium">Open a requisition</h2>
            <div className="mt-3.5 space-y-3">
              <Field label="Role title">
                <Input
                  autoFocus
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="Senior Payments Engineer"
                />
              </Field>
              <Field label="What the role is for" hint="Optional. Context for whoever writes the rubric.">
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={3}
                  className="w-full resize-y rounded-[var(--radius-sm)] border border-[var(--rule-strong)] bg-[var(--surface)] px-3 py-2 text-[13.5px] placeholder:text-[var(--ink-4)] hover:border-[var(--ink-4)] focus:border-[var(--ink)] focus:outline-none"
                  placeholder="Owns settlement and reconciliation for card payments."
                />
              </Field>
              <label className="flex cursor-pointer items-start gap-2.5 rounded-[var(--radius-sm)] border border-[var(--rule)] p-3">
                <input
                  type="checkbox"
                  checked={blind}
                  onChange={(e) => setBlind(e.target.checked)}
                  className="mt-0.5 accent-[var(--ink)]"
                />
                <span>
                  <span className="block text-[13px] font-medium">Blind screening</span>
                  <span className="mt-0.5 block text-[12px] leading-relaxed text-[var(--ink-3)]">
                    The model judging each criterion never sees the candidate&rsquo;s name,
                    contact details, institutions, or demographic details. You still see
                    the full CV.
                  </span>
                </span>
              </label>
              <div className="flex gap-2 pt-1">
                <Button
                  variant="solid"
                  onClick={() => create.mutate()}
                  loading={create.isPending}
                  disabled={!title.trim()}
                >
                  Open
                </Button>
                {list.length > 0 && (
                  <Button variant="quiet" onClick={() => setOpening(false)}>
                    Cancel
                  </Button>
                )}
              </div>
            </div>
          </Panel>
        )}

        {requisitions.isPending ? (
          <div className="flex justify-center py-16 text-[var(--ink-3)]">
            <Spinner />
          </div>
        ) : requisitions.isError ? (
          <Empty
            title="Requisitions could not be loaded"
            body={(requisitions.error as ApiError).message}
          />
        ) : list.length === 0 ? null : (
          <ul className="divide-y divide-[var(--rule)] rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--surface)]">
            {list.map((requisition) => (
              <li key={requisition.id}>
                <Link
                  href={`/hiring/${requisition.id}`}
                  className="group flex items-baseline justify-between gap-6 px-4 py-3.5 transition-colors hover:bg-[var(--surface-sunk)]"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[14px] font-medium">{requisition.title}</span>
                    {requisition.description && (
                      <span className="mt-0.5 block truncate text-[12.5px] text-[var(--ink-3)]">
                        {requisition.description}
                      </span>
                    )}
                  </span>
                  <span className="shrink-0 text-[12px] text-[var(--ink-3)]">
                    Opened {new Date(requisition.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
