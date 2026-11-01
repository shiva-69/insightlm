"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useSyncExternalStore } from "react";
import { clearSession, currentClaims, getEmail, getToken, subscribe } from "@/lib/auth";
import { ThemeToggle } from "./theme-toggle";
import { Button } from "./ui/primitives";

/* A 68px rail rather than a 240px sidebar. The main view is two documents side by
   side; every pixel the chrome takes is a pixel of reading width. Labels sit under
   the icons so nothing depends on a hover tooltip to be discoverable. */

/* Who sees which destination. Mirrors the server's permissions so nobody is offered a
   page that will only refuse them; the server still decides. */
const SCORES = ["owner", "recruiter", "hiring_manager", "auditor"];
const PANELS = ["owner", "recruiter", "hiring_manager", "interviewer"];

const NAV: { href: string; label: string; icon: () => React.JSX.Element; roles?: string[] }[] = [
  { href: "/ask", label: "Ask", icon: AskIcon },
  { href: "/documents", label: "Library", icon: LibraryIcon },
  { href: "/hiring", label: "Hiring", icon: HiringIcon, roles: SCORES },
  { href: "/interviews", label: "Interviews", icon: InterviewsIcon, roles: PANELS },
  { href: "/talent", label: "Talent", icon: TalentIcon, roles: SCORES },
  { href: "/team", label: "Team", icon: TeamIcon },
  { href: "/contracts", label: "Contracts", icon: LibraryIcon, roles: ["owner", "contract_manager", "counsel", "auditor"] },
  { href: "/audit", label: "Audit", icon: AuditIcon },
  { href: "/governance", label: "Fairness", icon: GovernanceIcon, roles: ["owner", "auditor"] },
];

export function Rail() {
  const pathname = usePathname();
  const router = useRouter();
  // The session is an external store; read it as one. The server snapshot is null,
  // so the first client render matches the HTML and the email fills in after.
  const email = useSyncExternalStore(subscribe, getEmail, () => null);
  // The token string is the stable snapshot; the role is derived from it.
  const token = useSyncExternalStore(subscribe, getToken, () => null);
  const role = useMemo(() => (token ? currentClaims()?.role : undefined), [token]);
  const visible = NAV.filter((item) => !item.roles || (role !== undefined && item.roles.includes(role)));

  function signOut() {
    clearSession();
    router.replace("/login");
  }

  return (
    <nav className="rule-r flex w-[68px] shrink-0 flex-col items-center bg-[var(--surface-sunk)] py-3">
      <Link
        href="/ask"
        className="mb-4 flex size-8 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--ink)] text-[var(--paper)]"
        aria-label="InsightLM"
      >
        <span className="font-serif text-[15px] leading-none">I</span>
      </Link>

      <ul className="flex w-full flex-1 flex-col items-center gap-0.5">
        {visible.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <li key={href} className="w-full px-2">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "group flex flex-col items-center gap-1 rounded-[var(--radius-sm)] py-2",
                  "transition-colors duration-150",
                  active
                    ? "bg-[var(--surface)] text-[var(--ink)] shadow-[inset_0_0_0_1px_var(--rule)]"
                    : "text-[var(--ink-3)] hover:bg-[var(--surface)]/60 hover:text-[var(--ink-2)]",
                )}
              >
                <Icon />
                <span className="text-[10.5px] font-medium tracking-[-0.005em]">{label}</span>
              </Link>
            </li>
          );
        })}
      </ul>

      <div className="flex flex-col items-center gap-1.5 pt-3">
        <ThemeToggle />
        <Button
          variant="quiet"
          size="sm"
          onClick={signOut}
          className="w-7 px-0"
          aria-label={email ? `Sign out ${email}` : "Sign out"}
          title={email ?? undefined}
        >
          <svg viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
            <path d="M6 14H3.5A1.5 1.5 0 0 1 2 12.5v-9A1.5 1.5 0 0 1 3.5 2H6" strokeLinecap="round" />
            <path d="M10.5 11 14 8l-3.5-3M14 8H6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </Button>
      </div>
    </nav>
  );
}

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

function AskIcon() {
  return (
    <svg viewBox="0 0 18 18" className="size-[18px]" aria-hidden {...stroke}>
      <path d="M2.5 4.5A1.5 1.5 0 0 1 4 3h10a1.5 1.5 0 0 1 1.5 1.5v6A1.5 1.5 0 0 1 14 12H7l-4 3.5V12H4a1.5 1.5 0 0 1-1.5-1.5Z" />
      <path d="M6.5 7.5h5M6.5 9.5h3" />
    </svg>
  );
}

function LibraryIcon() {
  return (
    <svg viewBox="0 0 18 18" className="size-[18px]" aria-hidden {...stroke}>
      <path d="M3 3.5h3.5v11H3zM8 3.5h3v11H8z" />
      <path d="m12.7 4.2 2.6.7-2.4 9.6-2.6-.7z" />
    </svg>
  );
}

function HiringIcon() {
  return (
    <svg viewBox="0 0 18 18" className="size-[18px]" aria-hidden {...stroke}>
      <rect x="2.5" y="5" width="13" height="9.5" rx="1.5" />
      <path d="M6.5 5V3.8a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V5M2.5 9.5h13" />
    </svg>
  );
}

function InterviewsIcon() {
  return (
    <svg viewBox="0 0 18 18" className="size-[18px]" aria-hidden {...stroke}>
      <rect x="3" y="2.5" width="12" height="13" rx="1.5" />
      <path d="M6 6.5h6M6 9h6M6 11.5h3.5" />
    </svg>
  );
}

function TalentIcon() {
  return (
    <svg viewBox="0 0 18 18" className="size-[18px]" aria-hidden {...stroke}>
      <circle cx="8" cy="8" r="4.5" />
      <path d="m11.5 11.5 3.5 3.5" />
    </svg>
  );
}

function GovernanceIcon() {
  return (
    <svg viewBox="0 0 18 18" className="size-[18px]" aria-hidden {...stroke}>
      <path d="M9 2.5v13M4 15.5h10" />
      <path d="M3 6h12M5.5 6 3 11a2.5 2.5 0 0 0 5 0L5.5 6ZM12.5 6 10 11a2.5 2.5 0 0 0 5 0l-2.5-5Z" />
    </svg>
  );
}

function TeamIcon() {
  return (
    <svg viewBox="0 0 18 18" className="size-[18px]" aria-hidden {...stroke}>
      <circle cx="7" cy="6" r="2.5" />
      <path d="M2.5 14.5c0-2.2 2-4 4.5-4s4.5 1.8 4.5 4" />
      <path d="M12 4.2a2.5 2.5 0 0 1 0 4.6M13.5 14.5c0-1.6-.6-2.6-1.5-3.4" />
    </svg>
  );
}

function AuditIcon() {
  return (
    <svg viewBox="0 0 18 18" className="size-[18px]" aria-hidden {...stroke}>
      <path d="M4.5 2.5h6L14 6v9.5H4.5z" />
      <path d="M10 2.5V6h3.5M7 9.5h4M7 12h4" />
    </svg>
  );
}
