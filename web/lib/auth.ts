"use client";

/** The browser's view of the session.
 *
 *  The token itself lives in an httpOnly cookie set by the API at sign-in. No script on
 *  the page can read it, which is the point: an injected script can no longer copy the
 *  session out of storage. What this module keeps is only what the interface needs to
 *  shape itself -- who is signed in and their role -- fetched from `/auth/session` and
 *  cached in `sessionStorage` so a reload paints without waiting. None of it grants
 *  access; the server authorises every request.
 */

const SESSION_KEY = "insightlm.session";
const CSRF_COOKIE = "insightlm_csrf";

export interface Session {
  userId: string;
  role: string;
  email: string | null;
}

const listeners = new Set<() => void>();
let cached: Session | null | undefined;

function notify() {
  for (const listener of listeners) listener();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function store(session: Session | null): void {
  cached = session;
  try {
    if (session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* private mode: the in-memory copy still serves this tab */
  }
  notify();
}

/** The cached session, or null. A stable reference between changes, so it can be read
 *  with useSyncExternalStore. */
export function getSession(): Session | null {
  if (typeof window === "undefined") return null;
  if (cached === undefined) {
    try {
      const raw = sessionStorage.getItem(SESSION_KEY);
      cached = raw ? (JSON.parse(raw) as Session) : null;
    } catch {
      cached = null;
    }
  }
  return cached;
}

export function getEmail(): string | null {
  return getSession()?.email ?? null;
}

export function currentClaims(): { userId: string; role: string } | null {
  const session = getSession();
  return session ? { userId: session.userId, role: session.role } : null;
}

/** The CSRF value the API set beside the session cookie. Echoed on every write. */
export function csrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]!) : null;
}

/** Ask the server who the cookie belongs to. Null when there is no live session. */
export async function refreshSession(email?: string): Promise<Session | null> {
  try {
    const response = await fetch("/api/auth/session", { cache: "no-store" });
    if (!response.ok) {
      store(null);
      return null;
    }
    const body = (await response.json()) as { user_id: string; role: string };
    const session = { userId: body.user_id, role: body.role, email: email ?? getEmail() };
    store(session);
    return session;
  } catch {
    return getSession();
  }
}

/** After a successful sign-in: the cookie is already set; learn who it belongs to. */
export async function startSession(email: string): Promise<void> {
  await refreshSession(email);
}

/** Forget the session locally, without telling the server (it already knows: a 401). */
export function forgetSession(): void {
  store(null);
}

/** Sign out: clear the cookies on the server and the cache here. */
export function clearSession(): void {
  store(null);
  void fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined);
}
