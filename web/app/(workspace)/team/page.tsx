"use client";

import clsx from "clsx";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Loading, QueryError } from "@/components/query-state";
import { useToast } from "@/components/toast";
import { Button, Empty, Field, Input, Panel, Spinner, Tag } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { currentClaims } from "@/lib/auth";
import type { Invitation, Role } from "@/lib/types";

/* Ownership is deliberately absent: it is transferred through its own flow, never
   handed out by invitation. The server refuses it too — this list just avoids
   offering something that would be rejected. */
export default function TeamPage() {
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("recruiter");
  const [issued, setIssued] = useState<Invitation | null>(null);

  const members = useQuery({ queryKey: ["members"], queryFn: api.members });
  // Roles come from the server, assembled from every vertical's declarations, so a new
  // vertical's roles appear here without a change to this page.
  const roles = useQuery({ queryKey: ["roles"], queryFn: api.roles, staleTime: Infinity });
  const INVITABLE = (roles.data ?? []).filter((r) => r.assignable);
  const ROLE_LABEL: Record<string, string> = Object.fromEntries(
    (roles.data ?? []).map((r) => [r.role, r.label]),
  );

  const invite = useMutation({
    mutationFn: () => api.invite({ email: email.trim(), role }),
    onSuccess: (invitation) => {
      setIssued(invitation);
      setEmail("");
      queryClient.invalidateQueries({ queryKey: ["members"] });
    },
    onError: (error) => {
      const failure = error as ApiError;
      notify(failure.message || "Could not send the invitation", {
        tone: "error",
        detail: failure.correlationId ? `ref ${failure.correlationId}` : undefined,
      });
    },
  });

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto max-w-[56rem] px-7 py-8">
        <header className="mb-6">
          <h1 className="font-serif text-[25px] leading-tight tracking-[-0.012em]">Team</h1>
          <p className="mt-1 text-[13px] text-[var(--ink-3)]">
            Roles decide what each person can see and do. Every change is written to
            the audit log.
          </p>
        </header>

        <Panel className="p-5">
          <h2 className="text-[13.5px] font-medium">Invite someone</h2>
          <div className="mt-3.5 grid gap-3 sm:grid-cols-[1fr_auto]">
            <Field label="Email">
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="colleague@company.com"
              />
            </Field>
            <div className="flex items-end">
              <Button
                variant="solid"
                onClick={() => invite.mutate()}
                loading={invite.isPending}
                disabled={!email.includes("@")}
              >
                Create invitation
              </Button>
            </div>
          </div>

          <fieldset className="mt-4">
            <legend className="mb-2 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
              Role
            </legend>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {INVITABLE.map((option) => (
                <label
                  key={option.role}
                  className={clsx(
                    "flex cursor-pointer items-start gap-2.5 rounded-[var(--radius-sm)] border p-2.5",
                    "transition-colors duration-150",
                    role === option.role
                      ? "border-[var(--ink)] bg-[var(--surface-sunk)]"
                      : "border-[var(--rule)] hover:border-[var(--rule-strong)]",
                  )}
                >
                  <input
                    type="radio"
                    name="role"
                    checked={role === option.role}
                    onChange={() => setRole(option.role)}
                    className="sr-only"
                  />
                  <span
                    className={clsx(
                      "mt-[3px] size-2.5 shrink-0 rounded-full border transition-colors",
                      role === option.role
                        ? "border-[var(--ink)] bg-[var(--ink)]"
                        : "border-[var(--rule-strong)]",
                    )}
                    aria-hidden
                  />
                  <span className="min-w-0">
                    <span className="block text-[12.5px] font-medium">{option.label}</span>
                    <span className="mt-0.5 block text-[11.5px] leading-snug text-[var(--ink-3)]">
                      {option.description}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        </Panel>

        {issued && <IssuedInvitation invitation={issued} onDismiss={() => setIssued(null)} />}

        <section className="mt-7">
          <h2 className="mb-2.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
            Members
          </h2>

          {members.isLoading && (
            <p className="flex items-center gap-2 py-6 text-[13px] text-[var(--ink-3)]">
              <Spinner /> Loading…
            </p>
          )}

          {members.isPending && <Loading />}
          {members.isError && (
            <QueryError error={members.error} retry={() => members.refetch()} what="the team" />
          )}
          {members.data?.length === 0 && (
            <Empty title="Just you so far" body="Invite the people who will work on this with you." />
          )}

          {members.data && members.data.length > 0 && (
            <ul className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--rule)]">
              {members.data.map((member, index) => (
                <li
                  key={member.membership.id}
                  className={clsx(
                    "flex items-center gap-3 bg-[var(--surface)] px-4 py-3",
                    index > 0 && "rule-t",
                    member.membership.revoked_at && "opacity-50",
                  )}
                >
                  <span className="flex size-7 shrink-0 items-center justify-center rounded-full border border-[var(--rule-strong)] bg-[var(--surface-sunk)] text-[11px] font-medium text-[var(--ink-2)]">
                    {member.user.display_name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px]">
                      {member.user.display_name}
                    </span>
                    <span className="data mt-0.5 block truncate">{member.user.email}</span>
                  </span>
                  <Tag tone={member.membership.role === "owner" ? "neutral" : "neutral"}>
                    {ROLE_LABEL[member.membership.role] ?? member.membership.role}
                  </Tag>
                </li>
              ))}
            </ul>
          )}
        </section>

        <TwoFactorPanel />
      </div>
    </div>
  );
}

/* Two-factor sign-in for whoever is looking at this page. Owners are urged to turn it
   on: an owner's account controls the workspace, its members and its erasure. */
function TwoFactorPanel() {
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["mfa"], queryFn: api.mfaStatus });
  const [enrolment, setEnrolment] = useState<{ secret: string; provisioning_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const owner = currentClaims()?.role === "owner";

  const done = (message: string) => () => {
    setEnrolment(null);
    setCode("");
    queryClient.invalidateQueries({ queryKey: ["mfa"] });
    notify(message);
  };
  const failed = (error: unknown) => notify((error as ApiError).message || "That did not work", { tone: "error" });
  const enrol = useMutation({ mutationFn: api.mfaEnrol, onSuccess: setEnrolment, onError: failed });
  const confirm = useMutation({
    mutationFn: () => api.mfaConfirm(code),
    onSuccess: done("Two-factor sign-in is on"),
    onError: failed,
  });
  const disable = useMutation({
    mutationFn: () => api.mfaDisable(code),
    onSuccess: done("Two-factor sign-in is off"),
    onError: failed,
  });

  if (status.isPending || status.isError) return null;
  const enabled = status.data.enabled;
  const codeInput = (
    <div className="w-32 shrink-0">
    <Input
      inputMode="numeric"
      autoComplete="one-time-code"
      aria-label="Authenticator code"
      value={code}
      onChange={(e) => setCode(e.target.value.replace(/[^0-9]/g, "").slice(0, 6))}
      placeholder="123456"
    />
    </div>
  );

  return (
    <section className="mt-7">
      <h2 className="mb-2.5 text-[11.5px] font-medium uppercase tracking-[0.07em] text-[var(--ink-3)]">
        Two-factor sign-in
      </h2>
      <Panel className="p-4">
        {enabled ? (
          <div className="flex flex-wrap items-center gap-3">
            <Tag tone="positive">On</Tag>
            <span className="flex-1 text-[13px] text-[var(--ink-2)]">Sign-in asks for a code from your authenticator app.</span>
            {codeInput}
            <Button variant="quiet" disabled={code.length !== 6} loading={disable.isPending} onClick={() => disable.mutate()}>
              Turn off
            </Button>
          </div>
        ) : enrolment ? (
          <div className="space-y-3 text-[13px]">
            <p>
              Add this key to your authenticator app (or open the link on your phone), then enter the
              code it shows. The key is shown only now.
            </p>
            <code className="data block break-all rounded-[var(--radius-sm)] bg-[var(--surface-sunk)] px-3 py-2">
              {enrolment.secret}
            </code>
            <a className="block break-all text-[12px] underline" href={enrolment.provisioning_uri}>
              Open in authenticator app
            </a>
            <div className="flex items-center gap-2">
              {codeInput}
              <Button variant="solid" disabled={code.length !== 6} loading={confirm.isPending} onClick={() => confirm.mutate()}>
                Turn on
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            {owner && <Tag tone="caution">Recommended for owners</Tag>}
            <span className="flex-1 text-[13px] text-[var(--ink-2)]">
              {owner
                ? "Your account controls this workspace. Protect it with a code from an authenticator app."
                : "Protect your account with a code from an authenticator app."}
            </span>
            <Button variant="outline" loading={enrol.isPending} onClick={() => enrol.mutate()}>
              Set up
            </Button>
          </div>
        )}
      </Panel>
    </section>
  );
}

/* The token is returned exactly once — only its hash is stored — so the UI has to
   be explicit that this is the only chance to copy it. */
function IssuedInvitation({
  invitation,
  onDismiss,
}: {
  invitation: Invitation;
  onDismiss: () => void;
}) {
  const { notify } = useToast();
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(joinLink(invitation.token));
    setCopied(true);
    notify("Invitation link copied");
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <Panel className="mt-4 border-[var(--evidence-edge)] p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="text-[13.5px] font-medium">
            Invitation for {invitation.email}
          </h3>
          <p className="mt-1 text-[12.5px] leading-relaxed text-[var(--ink-3)]">
            Copy this now — it is shown once and cannot be recovered. Only a hash of it
            is stored. Expires{" "}
            {new Date(invitation.expires_at).toLocaleDateString(undefined, {
              day: "numeric",
              month: "long",
            })}
            .
          </p>
        </div>
        <Button variant="quiet" size="sm" onClick={onDismiss} aria-label="Dismiss">
          ✕
        </Button>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <code className="data flex-1 truncate rounded-[var(--radius-xs)] border border-[var(--rule)] bg-[var(--surface-sunk)] px-2.5 py-2 text-[var(--ink-2)]">
          {joinLink(invitation.token)}
        </code>
        <Button size="sm" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </Panel>
  );
}

function joinLink(token: string): string {
  return `${window.location.origin}/join?token=${encodeURIComponent(token)}`;
}
