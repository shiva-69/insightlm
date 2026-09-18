"use client";

import clsx from "clsx";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

type Tone = "info" | "error";

interface Toast {
  id: number;
  tone: Tone;
  message: string;
  detail?: string;
}

const ToastContext = createContext<{
  notify: (message: string, options?: { tone?: Tone; detail?: string }) => void;
} | null>(null);

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const notify = useCallback(
    (message: string, options?: { tone?: Tone; detail?: string }) => {
      const id = Date.now() + Math.random();
      setToasts((current) => [
        ...current,
        { id, message, tone: options?.tone ?? "info", detail: options?.detail },
      ]);
      // Errors stay longer: they usually carry a correlation id worth reading.
      const ttl = options?.tone === "error" ? 8000 : 4000;
      setTimeout(() => setToasts((c) => c.filter((t) => t.id !== id)), ttl);
    },
    [],
  );

  const value = useMemo(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 left-1/2 z-50 flex w-full max-w-md -translate-x-1/2 flex-col gap-2 px-4"
        role="status"
        aria-live="polite"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={clsx(
              "pointer-events-auto rounded-[var(--radius-md)] border px-3.5 py-2.5",
              "bg-[var(--surface)] shadow-[var(--shadow-pop)]",
              "animate-[toast-in_180ms_var(--ease-out-quick)]",
              toast.tone === "error"
                ? "border-[var(--caution)]/40"
                : "border-[var(--rule-strong)]",
            )}
          >
            <p
              className={clsx(
                "text-[13px] font-medium",
                toast.tone === "error" ? "text-[var(--caution)]" : "text-[var(--ink)]",
              )}
            >
              {toast.message}
            </p>
            {toast.detail && <p className="data mt-1">{toast.detail}</p>}
          </div>
        ))}
      </div>
      <style>{`@keyframes toast-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </ToastContext.Provider>
  );
}
