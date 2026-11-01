"use client";

import { useSyncExternalStore } from "react";

/* Below `lg`, where the evidence pane no longer fits beside the text it supports. */
const QUERY = "(max-width: 1023.98px)";

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia(QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

export function useNarrow(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => false,
  );
}
