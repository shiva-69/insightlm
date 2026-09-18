"use client";

/** Session storage.
 *
 *  The access token lives in `sessionStorage`, not `localStorage`: it expires in
 *  fifteen minutes anyway, and scoping it to the tab means closing the tab ends the
 *  session. Neither is a substitute for an httpOnly cookie, which is what this would
 *  use in production — noted here rather than pretended otherwise.
 */

const TOKEN_KEY = "insightlm.token";
const EMAIL_KEY = "insightlm.email";

const listeners = new Set<() => void>();

function notify() {
  for (const listener of listeners) listener();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getEmail(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return sessionStorage.getItem(EMAIL_KEY);
  } catch {
    return null;
  }
}

export function startSession(token: string, email: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
    sessionStorage.setItem(EMAIL_KEY, email);
  } catch {
    /* private mode — the app still works for this tab, just not across reloads */
  }
  notify();
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(EMAIL_KEY);
  } catch {
    /* nothing to clear */
  }
  notify();
}

/** Who the token says we are -- for shaping the UI only.
 *
 *  Decoded, not verified: the browser has no key to verify with, and does not need
 *  one. Hiding a button someone cannot use is a courtesy; the server refusing the
 *  request is the control.
 */
export function currentClaims(): { userId: string; role: string } | null {
  const token = getToken();
  if (!token) return null;
  try {
    const payload = token.split(".")[1] ?? "";
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    return { userId: String(json.sub), role: String(json.role) };
  } catch {
    return null;
  }
}
