"use client";

import { ApiError } from "@/lib/api";
import { Button, Panel, Spinner } from "./ui/primitives";

/* A failed load must never look like an empty one: "no candidates yet" when the request
   actually failed sends a recruiter to re-add people who are already there. */
export function QueryError({
  error,
  retry,
  what,
}: {
  error: unknown;
  retry: () => void;
  what: string;
}) {
  const failure = error instanceof ApiError ? error : null;
  return (
    <div role="alert">
      <Panel className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="min-w-0">
          <p className="text-[13px] font-medium">Couldn&rsquo;t load {what}</p>
          <p className="mt-0.5 text-[12px] text-[var(--ink-3)]">
            {failure?.message || "The server didn't respond."}
            {failure?.correlationId && (
              <span className="data"> · ref {failure.correlationId}</span>
            )}
          </p>
        </div>
        <Button size="sm" onClick={retry}>
          Try again
        </Button>
      </Panel>
    </div>
  );
}

export function Loading() {
  return (
    <div
      className="flex justify-center py-10 text-[var(--ink-3)]"
      aria-busy="true"
      aria-label="Loading"
    >
      <Spinner />
    </div>
  );
}
