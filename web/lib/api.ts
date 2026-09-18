import type {
  Answer,
  AuditEntry,
  ChainVerification,
  DocumentSummary,
  DocumentVersion,
  Invitation,
  Member,
  Registration,
  Role,
  TokenResponse,
  UploadResult,
  ApiErrorBody,
} from "./types";
import { getToken, clearSession } from "./auth";

/** Carries the server's error code and correlation id through to the UI, so a
 *  failure can be reported with something an operator can actually grep for. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly correlationId: string | null = null,
    readonly detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const { json, ...rest } = init;
  const headers = new Headers(rest.headers);

  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    rest.body = JSON.stringify(json);
  }

  const response = await fetch(`/api${path}`, { ...rest, headers });

  if (response.status === 401 && token) {
    // The token expired underneath us. Drop it rather than leaving the app in a
    // half-authenticated state where every subsequent call fails the same way.
    clearSession();
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
    throw new ApiError(
      response.status,
      body?.code ?? "unknown",
      body?.message ?? response.statusText,
      body?.correlation_id ?? response.headers.get("X-Correlation-ID"),
      body?.detail ?? {},
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  register: (input: {
    tenant_name: string;
    tenant_slug: string;
    email: string;
    display_name: string;
    password: string;
  }) => request<Registration>("/auth/register", { method: "POST", json: input }),

  login: (input: { email: string; password: string; tenant_slug?: string }) =>
    request<TokenResponse>("/auth/login", { method: "POST", json: input }),

  acceptInvitation: (input: {
    token: string;
    display_name: string;
    password: string;
  }) => request<Registration>("/auth/invitations/accept", { method: "POST", json: input }),

  members: () => request<Member[]>("/members"),

  invite: (input: { email: string; role: Role }) =>
    request<Invitation>("/members/invitations", { method: "POST", json: input }),

  changeRole: (membershipId: string, role: Role) =>
    request<Member["membership"]>(`/members/${membershipId}/role`, {
      method: "PATCH",
      json: { role },
    }),

  revokeMember: (membershipId: string) =>
    request<Member["membership"]>(`/members/${membershipId}`, { method: "DELETE" }),

  documents: () => request<DocumentSummary[]>("/documents"),

  documentStatus: (documentId: string) =>
    request<DocumentVersion>(`/documents/${documentId}/status`),

  upload: async (file: File, title?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    // No Content-Type header: the browser must set the multipart boundary itself.
    return request<UploadResult>("/documents", { method: "POST", body: form });
  },

  ask: (input: { question: string; top_k?: number; document_ids?: string[] }) =>
    request<Answer>("/ask", { method: "POST", json: input }),

  auditEntries: (limit = 100) => request<AuditEntry[]>(`/audit/entries?limit=${limit}`),

  verifyChain: () => request<ChainVerification>("/audit/verify", { method: "POST" }),

  /** The raw file, for the viewer. Returns bytes, not JSON. */
  documentBytes: async (documentId: string): Promise<ArrayBuffer> => {
    const token = getToken();
    const response = await fetch(`/api/documents/${documentId}/raw`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) throw new ApiError(response.status, "fetch_failed", "Could not load the document");
    return response.arrayBuffer();
  },
};
