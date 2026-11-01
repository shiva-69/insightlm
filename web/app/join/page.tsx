"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useToast } from "@/components/toast";
import { Button, Field, Input } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { startSession } from "@/lib/auth";

/* Where an invitation link lands. The token comes in the URL; the person picks the
   name their teammates will see and a password, and is signed straight in. */
export default function JoinPage() {
  return (
    <Suspense>
      <Join />
    </Suspense>
  );
}

function Join() {
  const router = useRouter();
  const params = useSearchParams();
  const { notify } = useToast();
  const [token, setToken] = useState(params.get("token") ?? "");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const joined = await api.acceptInvitation({
        token: token.trim(),
        display_name: name.trim(),
        password,
      });
      await api.login({ email: joined.user.email, password });
      await startSession(joined.user.email);
      router.replace(joined.membership.role === "interviewer" ? "/interviews" : "/ask");
    } catch (error) {
      const failure = error as ApiError;
      notify(failure.message || "That invitation could not be used", {
        tone: "error",
        detail: "Invitations are single-use and expire. Ask for a new one if needed.",
      });
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-6 py-14">
      <form onSubmit={submit} className="w-full max-w-[22rem] space-y-4">
        <div>
          <span className="flex size-8 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--ink)] text-[var(--paper)]">
            <span className="font-serif text-[15px] leading-none">I</span>
          </span>
          <h1 className="mt-6 font-serif text-[23px] leading-tight tracking-[-0.01em]">
            Join your team
          </h1>
          <p className="mt-1 text-[13px] text-[var(--ink-3)]">
            Your role was chosen by whoever invited you.
          </p>
        </div>
        {!params.get("token") && (
          <Field label="Invitation code">
            <Input value={token} onChange={(e) => setToken(e.target.value)} autoFocus />
          </Field>
        )}
        <Field label="Your name" hint="What your teammates will see.">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus={Boolean(params.get("token"))}
            placeholder="Ada Mensah"
          />
        </Field>
        <Field label="Password" hint="At least 12 characters.">
          <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Button
          type="submit"
          variant="solid"
          className="w-full"
          loading={busy}
          disabled={!token.trim() || !name.trim() || password.length < 12}
        >
          Join
        </Button>
      </form>
    </div>
  );
}
