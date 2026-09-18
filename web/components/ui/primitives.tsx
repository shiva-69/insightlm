"use client";

import clsx from "clsx";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { forwardRef } from "react";

/* A deliberately small vocabulary. Every additional variant is a decision someone
   has to make on every screen, and inconsistency reads louder than any one style. */

type ButtonVariant = "solid" | "outline" | "quiet" | "danger";
type ButtonSize = "sm" | "md";

const buttonBase =
  "inline-flex items-center justify-center gap-1.5 font-medium whitespace-nowrap " +
  "transition-[background-color,border-color,color,opacity] duration-150 " +
  "disabled:opacity-40 disabled:pointer-events-none select-none";

const buttonVariants: Record<ButtonVariant, string> = {
  solid:
    "bg-[var(--ink)] text-[var(--paper)] border border-[var(--ink)] hover:opacity-88 active:opacity-95",
  outline:
    "bg-[var(--surface)] text-[var(--ink)] border border-[var(--rule-strong)] hover:bg-[var(--surface-sunk)]",
  quiet:
    "bg-transparent text-[var(--ink-2)] border border-transparent hover:bg-[var(--surface-sunk)] hover:text-[var(--ink)]",
  danger:
    "bg-transparent text-[var(--caution)] border border-transparent hover:bg-[var(--caution)]/10",
};

const buttonSizes: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-[12.5px] rounded-[var(--radius-xs)]",
  md: "h-9 px-3.5 text-[13.5px] rounded-[var(--radius-sm)]",
};

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: ButtonVariant;
    size?: ButtonSize;
    loading?: boolean;
  }
>(function Button(
  { variant = "outline", size = "md", loading, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={clsx(buttonBase, buttonVariants[variant], buttonSizes[size], className)}
      {...rest}
    >
      {loading && <Spinner />}
      {children}
    </button>
  );
});

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden
      className={clsx("size-3.5 shrink-0 animate-spin", className)}
    >
      <circle
        cx="8" cy="8" r="6.5" fill="none"
        stroke="currentColor" strokeWidth="2" opacity="0.2"
      />
      <path
        d="M8 1.5A6.5 6.5 0 0 1 14.5 8" fill="none"
        stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      />
    </svg>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return (
      <input
        ref={ref}
        className={clsx(
          "h-9 w-full rounded-[var(--radius-sm)] border border-[var(--rule-strong)]",
          "bg-[var(--surface)] px-3 text-[13.5px] text-[var(--ink)]",
          "placeholder:text-[var(--ink-4)]",
          "transition-colors duration-150 hover:border-[var(--ink-4)]",
          "focus:border-[var(--ink)] focus:outline-none",
          className,
        )}
        {...rest}
      />
    );
  },
);

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="block text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
        {label}
      </span>
      {children}
      {hint && <span className="block text-[12px] text-[var(--ink-3)]">{hint}</span>}
    </label>
  );
}

type Tone = "neutral" | "evidence" | "positive" | "caution";

const toneStyles: Record<Tone, string> = {
  neutral: "text-[var(--ink-3)] border-[var(--rule)]",
  evidence: "text-[var(--evidence)] border-[var(--evidence-edge)]",
  positive: "text-[var(--positive)] border-[var(--positive)]/35",
  caution: "text-[var(--caution)] border-[var(--caution)]/35",
};

export function Tag({
  tone = "neutral",
  children,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-[var(--radius-xs)] border px-1.5 py-0.5",
        "text-[11px] font-medium tracking-[-0.005em]",
        toneStyles[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const dotTones: Record<DocumentDot, string> = {
  pending: "bg-[var(--ink-4)]",
  processing: "bg-[var(--evidence)]",
  ready: "bg-[var(--positive)]",
  failed: "bg-[var(--caution)]",
};

type DocumentDot = "pending" | "processing" | "ready" | "failed";

export function StatusDot({ status }: { status: DocumentDot }) {
  return (
    <span className="relative inline-flex size-1.5 shrink-0" aria-hidden>
      {status === "processing" && (
        <span className="absolute inset-0 animate-ping rounded-full bg-[var(--evidence)] opacity-60" />
      )}
      <span className={clsx("size-1.5 rounded-full", dotTones[status])} />
    </span>
  );
}

/** Empty states earn their space by saying what to do next, not by apologising. */
export function Empty({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-sm px-6 py-16 text-center">
      <p className="font-serif text-[17px] text-[var(--ink)]">{title}</p>
      <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--ink-3)]">{body}</p>
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd
      className={clsx(
        "inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[3px]",
        "border border-[var(--rule-strong)] bg-[var(--surface-sunk)] px-1",
        "font-mono text-[10px] text-[var(--ink-3)]",
      )}
    >
      {children}
    </kbd>
  );
}

export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--surface)]",
        className,
      )}
    >
      {children}
    </div>
  );
}
