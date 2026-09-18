"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Empty, Input, Panel } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import type { TalentMatch } from "@/lib/types";

const EXAMPLES = [
  "Kubernetes in production",
  "payment reconciliation",
  "led a team",
  "React and TypeScript",
];

/* Search returns people, and for each person the passage that matched. No summary is
   written: what you read is what the CV says. */
export default function TalentPage() {
  const [query, setQuery] = useState("");
  const [asked, setAsked] = useState<string | null>(null);

  const search = useMutation({
    mutationFn: (q: string) => api.searchTalent(q),
    onMutate: (q) => setAsked(q),
  });

  function run(q: string) {
    const trimmed = q.trim();
    if (trimmed) search.mutate(trimmed);
  }

  const results = search.data ?? [];

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-[48rem] px-7 py-8">
        <header className="mb-5">
          <h1 className="font-serif text-[25px] leading-tight tracking-[-0.012em]">Talent pool</h1>
          <p className="mt-1 text-[13px] text-[var(--ink-3)]">
            Search every candidate&rsquo;s documents across all requisitions. Results are
            people, each with the passage that matched.
          </p>
        </header>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            run(query);
          }}
          className="flex gap-2"
        >
          <Input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Who has run Kubernetes in production?"
            aria-label="Search the talent pool"
          />
          <Button variant="solid" type="submit" loading={search.isPending} disabled={!query.trim()}>
            Search
          </Button>
        </form>

        {!asked && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                onClick={() => {
                  setQuery(example);
                  run(example);
                }}
                className="h-7 rounded-[var(--radius-xs)] border border-[var(--rule)] px-2.5 text-[12px] text-[var(--ink-3)] transition-colors hover:border-[var(--rule-strong)] hover:text-[var(--ink)]"
              >
                {example}
              </button>
            ))}
          </div>
        )}

        <div className="mt-7">
          {search.isError ? (
            <Empty title="Search failed" body={(search.error as ApiError).message} />
          ) : asked && search.isSuccess && results.length === 0 ? (
            <Panel>
              <Empty
                title="Nobody matched"
                body="No candidate's documents speak to that. Try the words a CV would use."
              />
            </Panel>
          ) : (
            <ol className="space-y-2.5">
              {results.map((match, index) => (
                <Result key={match.candidate.id} match={match} rank={index + 1} />
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}

function Result({ match, rank }: { match: TalentMatch; rank: number }) {
  return (
    <li>
      <Panel className="p-4">
        <div className="flex items-baseline gap-3">
          <span className="data w-4 text-right">{rank}</span>
          <span className="text-[14px] font-medium">{match.candidate.name}</span>
          {match.candidate.email && (
            <span className="truncate text-[12px] text-[var(--ink-3)]">{match.candidate.email}</span>
          )}
        </div>
        <ul className="mt-2.5 space-y-1.5 pl-7">
          {match.passages.map((passage) => (
            <li
              key={passage.label}
              className="rounded-[var(--radius-sm)] border border-[var(--evidence-edge)] bg-[var(--evidence-fill)] px-3 py-2"
            >
              <span className="line-clamp-3 font-serif text-[13.5px] leading-snug text-[var(--ink-2)]">
                {passage.text}
              </span>
              <span className="data mt-1 block">
                {passage.document_title} · p{passage.page + 1}
              </span>
            </li>
          ))}
        </ul>
      </Panel>
    </li>
  );
}
