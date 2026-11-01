"use client";

import { useEffect, useRef } from "react";
import { Tag } from "./ui/primitives";

/* The evidence pane on a phone.
 *
 * On a wide screen the source sits beside the answer, so checking a claim is a glance.
 * A phone has room for one of them, so tapping a citation opens the source over the
 * answer, already scrolled to the highlight, and "Back" returns to the sentence that
 * cited it. Only a tap opens it: hover activation, which a tap also fires, must not.
 */
export function SourceSheet({
  open,
  title,
  highlighted,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  highlighted: number;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    // Back returns focus to the citation that opened the sheet, not to the top of the page.
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Source: ${title}`}
      className="fixed inset-0 z-40 flex flex-col bg-[var(--surface-sunk)] lg:hidden"
    >
      <div className="rule-b flex h-12 shrink-0 items-center gap-2 bg-[var(--surface)] px-2 pt-[env(safe-area-inset-top)]">
        <button
          ref={closeRef}
          onClick={onClose}
          className="flex h-9 items-center gap-1 rounded-[var(--radius-sm)] px-2.5 text-[13px] text-[var(--ink-2)] hover:bg-[var(--surface-sunk)]"
        >
          <span aria-hidden>←</span> Back
        </button>
        <span className="min-w-0 flex-1 truncate text-[12.5px] text-[var(--ink-2)]">{title}</span>
        {highlighted > 0 && <Tag tone="evidence">{highlighted} highlighted</Tag>}
      </div>
      <div className="min-h-0 flex-1 pb-[env(safe-area-inset-bottom)]">{children}</div>
    </div>
  );
}
