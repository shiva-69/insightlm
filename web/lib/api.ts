import type {
  Answer,
  ContractReview,
  Playbook,
  RoleInfo,
  Severity,
  Citation,
  Claim,
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
  Application,
  AssessmentJob,
  BatchAssessResult,
  BatchScope,
  Candidate,
  CriterionInput,
  Requisition,
  RequisitionDetail,
  Rubric,
  ShortlistEntry,
  ApplicationDetail,
  Interview,
  RejectionReason,
  Recommendation,
  Scorecard,
  Stage,
  TalentMatch,
  ContentFinding,
  ErasureReport,
  RetentionRun,
  SelectionReport,
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

  if (!response.ok) throw await errorFrom(response);

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function errorFrom(response: Response): Promise<ApiError> {
  const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
  return new ApiError(
    response.status,
    body?.code ?? "unknown",
    body?.message ?? response.statusText,
    body?.correlation_id ?? response.headers.get("X-Correlation-ID"),
    body?.detail ?? {},
  );
}

export interface AnswerStreamHandlers {
  /* The passages the answer may cite, before any claim is written. */
  onEvidence: (passages: Citation[]) => void;
  /* A claim the server has validated: safe to show. */
  onClaim: (claim: Claim) => void;
  /* Discard every claim shown so far; a corrected attempt follows. */
  onWithdrawn: (reason: string) => void;
}

/* Ask, and receive each validated claim as it is written. Resolves with the final
   answer -- the same body `/ask` returns -- once the server sends it. */
async function askStream(
  input: { question: string; top_k?: number; document_ids?: string[] },
  handlers: AnswerStreamHandlers,
): Promise<Answer> {
  const headers = new Headers({ "Content-Type": "application/json", Accept: "text/event-stream" });
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch("/api/ask/stream", {
    method: "POST",
    headers,
    body: JSON.stringify(input),
  });
  if (response.status === 401 && token) clearSession();
  if (!response.ok || !response.body) throw await errorFrom(response);

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = /^event: (.*)$/m.exec(block)?.[1];
      const data = JSON.parse(/^data: (.*)$/m.exec(block)?.[1] ?? "null");
      if (event === "evidence") handlers.onEvidence(data as Citation[]);
      else if (event === "claim") handlers.onClaim(data as Claim);
      else if (event === "withdrawn") handlers.onWithdrawn((data as { reason: string }).reason);
      else if (event === "done") return data as Answer;
      else if (event === "error") {
        const failure = data as { code: string; message: string };
        throw new ApiError(500, failure.code, failure.message);
      }
    }
  }
  throw new ApiError(502, "stream_ended", "The answer stopped before it was finished.");
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

  roles: () => request<RoleInfo[]>("/members/roles"),

  playbooks: () => request<Playbook[]>("/playbooks"),

  proposePlaybook: (input: {
    name: string;
    positions: { clause: string; standard: string; severity: Severity; evidence_hint?: string }[];
  }) => request<Playbook>("/playbooks", { method: "POST", json: input }),

  approvePlaybook: (id: string) => request<Playbook>(`/playbooks/${id}/approve`, { method: "POST" }),

  reviewContract: (input: { title: string; playbook_id: string; document_ids: string[] }) =>
    request<ContractReview>("/contracts/reviews", { method: "POST", json: input }),

  contractReviews: () => request<ContractReview[]>("/contracts/reviews"),

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

  askStream,

  auditEntries: (limit = 100) => request<AuditEntry[]>(`/audit/entries?limit=${limit}`),

  verifyChain: () => request<ChainVerification>("/audit/verify", { method: "POST" }),

  requisitions: () => request<Requisition[]>("/requisitions"),

  createRequisition: (input: { title: string; description: string; blind_screening: boolean }) =>
    request<Requisition>("/requisitions", { method: "POST", json: input }),

  requisition: (id: string) => request<RequisitionDetail>(`/requisitions/${id}`),

  proposeRubric: (requisitionId: string, criteria: CriterionInput[]) =>
    request<Rubric>(`/requisitions/${requisitionId}/rubrics`, {
      method: "POST",
      json: { criteria },
    }),

  approveRubric: (rubricId: string) =>
    request<Rubric>(`/rubrics/${rubricId}/approve`, { method: "POST" }),

  addCandidate: (
    requisitionId: string,
    input: { name: string; email?: string; document_ids: string[] },
  ) =>
    request<{ candidate: Candidate; application: Application }>(
      `/requisitions/${requisitionId}/candidates`,
      { method: "POST", json: input },
    ),

  /* Queues the assessment and returns at once; the worker runs it and the shortlist
     shows its progress. */
  assess: (applicationId: string) =>
    request<AssessmentJob>(`/applications/${applicationId}/assessments`, { method: "POST" }),

  assessRequisition: (requisitionId: string, scope: BatchScope) =>
    request<BatchAssessResult>(`/requisitions/${requisitionId}/assessments`, {
      method: "POST",
      json: { scope },
    }),

  shortlist: (requisitionId: string) =>
    request<ShortlistEntry[]>(`/requisitions/${requisitionId}/shortlist`),

  application: (id: string) => request<ApplicationDetail>(`/applications/${id}`),

  moveApplication: (
    id: string,
    input: { to: Stage; reason?: RejectionReason; note?: string },
  ) => request<Application>(`/applications/${id}/stage`, { method: "POST", json: input }),

  assignInterviewer: (applicationId: string, userId: string) =>
    request<Scorecard>(`/applications/${applicationId}/panel`, {
      method: "POST",
      json: { user_id: userId },
    }),

  submitScorecard: (
    id: string,
    input: {
      ratings: { criterion_id: string; rating: number; note: string }[];
      recommendation: Recommendation;
      summary: string;
    },
  ) => request<Scorecard>(`/scorecards/${id}/submit`, { method: "POST", json: input }),

  myInterviews: () => request<Interview[]>("/me/interviews"),

  candidateRecord: (candidateId: string) =>
    request<Record<string, unknown>>(`/candidates/${candidateId}/record`),

  eraseCandidate: (candidateId: string) =>
    request<ErasureReport>(`/candidates/${candidateId}/erasure`, { method: "POST" }),

  selectionRates: (requisitionId?: string) =>
    request<SelectionReport>(
      `/governance/selection-rates${requisitionId ? `?requisition_id=${requisitionId}` : ""}`,
    ),

  retention: (dryRun: boolean) =>
    request<RetentionRun>(`/governance/retention/run?dry_run=${dryRun}`, { method: "POST" }),

  findings: (documentId: string) =>
    request<ContentFinding[]>(`/documents/${documentId}/findings`),

  searchTalent: (q: string) =>
    request<TalentMatch[]>(`/talent/search?q=${encodeURIComponent(q)}`),

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
