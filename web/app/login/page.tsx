"use client";

import clsx from "clsx";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { startSession } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Button, Field, Input } from "@/components/ui/primitives";

type Mode = "signin" | "create";

export default function LoginPage() {
  const router = useRouter();
  const { notify } = useToast();
  const [mode, setMode] = useState<Mode>("signin");
  const [busy, setBusy] = useState(false);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [name, setName] = useState("");

  const slug = workspace
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      if (mode === "create") {
        await api.register({
          tenant_name: workspace.trim(),
          tenant_slug: slug,
          email: email.trim(),
          display_name: name.trim() || email.split("@")[0] || "Owner",
          password,
        });
      }
      await api.login({ email: email.trim(), password });
      await startSession(email.trim());
      router.replace("/ask");
    } catch (error) {
      const failure = error as ApiError;
      notify(failure.message || "Something went wrong", {
        tone: "error",
        detail: failure.correlationId ? `ref ${failure.correlationId}` : undefined,
      });
      setBusy(false);
    }
  }

  const canSubmit =
    email.includes("@") &&
    password.length >= 12 &&
    (mode === "signin" || slug.length >= 3);

  return (
    <div className="relative z-10 grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      {/* The statement side. A product thesis in the reading face, not a hero
          gradient — it says what the tool is for in the same voice the tool
          answers in. */}
      <section className="rule-r hidden flex-col justify-between bg-[var(--surface-sunk)] p-12 lg:flex xl:p-16">
        <div className="flex items-center gap-2.5">
          <span className="flex size-7 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--ink)] text-[var(--paper)]">
            <span className="font-serif text-[13px] leading-none">I</span>
          </span>
          <span className="text-[13px] font-medium tracking-[-0.01em]">InsightLM</span>
        </div>

        <div className="max-w-[30rem]">
          <h1 className="font-serif text-[34px] leading-[1.22] tracking-[-0.015em] text-[var(--ink)] xl:text-[40px]">
            Never trust the answer.
            <br />
            <span className="italic text-[var(--ink-2)]">Trust the evidence.</span>
          </h1>
          <p className="mt-5 max-w-[26rem] text-[14px] leading-relaxed text-[var(--ink-2)]">
            Ask a question of your documents and every sentence comes back with the
            passage it came from — highlighted, in the original, on the page where it
            actually appears.
          </p>
          <p className="mt-3 max-w-[26rem] text-[14px] leading-relaxed text-[var(--ink-3)]">
            A claim that cannot be traced to a source is never shown to you.
          </p>
        </div>

        <dl className="grid max-w-[30rem] grid-cols-3 gap-6">
          {[
            ["Cited", "every claim"],
            ["Refused", "when unsupported"],
            ["Logged", "tamper-evident"],
          ].map(([term, detail]) => (
            <div key={term}>
              <dt className="text-[12.5px] font-medium text-[var(--ink)]">{term}</dt>
              <dd className="mt-0.5 text-[12px] text-[var(--ink-3)]">{detail}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="flex items-center justify-center px-6 py-14">
        <div className="w-full max-w-[22rem]">
          <div className="mb-7 lg:hidden">
            <span className="flex size-8 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--ink)] text-[var(--paper)]">
              <span className="font-serif text-[15px] leading-none">I</span>
            </span>
          </div>

          <h2 className="font-serif text-[23px] leading-tight tracking-[-0.01em]">
            {mode === "signin" ? "Sign in" : "Create a workspace"}
          </h2>
          <p className="mt-1 text-[13px] text-[var(--ink-3)]">
            {mode === "signin"
              ? "Continue to your workspace."
              : "You'll be its owner. Invite your team once you're in."}
          </p>

          <form onSubmit={submit} className="mt-7 space-y-4">
            {mode === "create" && (
              <>
                <Field
                  label="Workspace"
                  hint={
                    slug ? (
                      <span className="data">insightlm.app/{slug}</span>
                    ) : (
                      "Your team or company name."
                    )
                  }
                >
                  <Input
                    value={workspace}
                    onChange={(e) => setWorkspace(e.target.value)}
                    placeholder="Acme Talent"
                    autoComplete="organization"
                    required
                  />
                </Field>
                <Field label="Your name">
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Priya Sharma"
                    autoComplete="name"
                  />
                </Field>
              </>
            )}

            <Field label="Email">
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                autoComplete="email"
                required
              />
            </Field>

            <Field
              label="Password"
              hint={
                mode === "create" && password.length > 0 && password.length < 12
                  ? `${12 - password.length} more characters`
                  : mode === "create"
                    ? "At least 12 characters."
                    : undefined
              }
            >
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••"
                autoComplete={mode === "create" ? "new-password" : "current-password"}
                required
              />
            </Field>

            <Button
              type="submit"
              variant="solid"
              loading={busy}
              disabled={!canSubmit}
              className="w-full"
            >
              {mode === "signin" ? "Sign in" : "Create workspace"}
            </Button>
          </form>

          <p className="mt-5 text-[13px] text-[var(--ink-3)]">
            {mode === "signin" ? "No workspace yet?" : "Already have one?"}{" "}
            <button
              type="button"
              onClick={() => setMode(mode === "signin" ? "create" : "signin")}
              className={clsx(
                "font-medium text-[var(--ink)] underline decoration-[var(--rule-strong)]",
                "underline-offset-[3px] transition-colors hover:decoration-[var(--ink)]",
              )}
            >
              {mode === "signin" ? "Create one" : "Sign in"}
            </button>
          </p>
        </div>
      </section>
    </div>
  );
}
