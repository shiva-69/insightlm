/** Mirrors the FastAPI response models. Kept hand-written and narrow: generating
 *  from OpenAPI would pull in every field the API happens to expose, and the point
 *  of a client type is to say what this app actually consumes. */

export type Role =
  | "owner"
  | "recruiter"
  | "hiring_manager"
  | "interviewer"
  | "coordinator"
  | "auditor";

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  created_at: string;
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  is_active: boolean;
}

export interface Membership {
  id: string;
  tenant_id: string;
  user_id: string;
  role: Role;
  created_at: string;
  revoked_at: string | null;
}

export interface Member {
  membership: Membership;
  user: User;
}

export interface Registration {
  tenant: Tenant;
  user: User;
  membership: Membership;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface Invitation {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  /** Returned exactly once. Only its hash is stored server-side. */
  token: string;
}

export interface DocumentSummary {
  id: string;
  title: string;
  media_type: string;
  created_at: string;
}

export interface DocumentVersion {
  id: string;
  document_id: string;
  version: number;
  status: DocumentStatus;
  byte_size: number;
  page_count: number | null;
  char_count: number | null;
  failure_reason: string | null;
}

export interface UploadResult {
  document: DocumentSummary;
  version: DocumentVersion;
  was_duplicate: boolean;
}

export interface Rect {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Citation {
  label: string;
  chunk_id: string;
  document_id: string;
  document_title: string;
  /** Zero-based. */
  page: number;
  char_start: number;
  char_end: number;
  quoted_text: string;
  rects: Rect[];
}

export interface Claim {
  text: string;
  /** Citation labels, resolved against `Answer.citations`. */
  citations: string[];
}

export type AnswerOutcome = "answered" | "insufficient_evidence" | "validation_failed";

export interface Answer {
  outcome: AnswerOutcome;
  text: string;
  claims: Claim[];
  citations: Citation[];
  refusal_reason: string | null;
  strategy: string;
  retrieval_config_hash: string;
  model_version: string;
  prompt_version: string;
  embedding_model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
}

export interface AuditEntry {
  id: string;
  actor_id: string | null;
  action: string;
  subject_type: string;
  subject_id: string | null;
  payload: Record<string, unknown>;
  occurred_at: string;
  prev_hash: string;
  entry_hash: string;
}

export interface ChainVerification {
  tenant_id: string;
  entries_checked: number;
  is_intact: boolean;
  broken_at: string | null;
  reason: string | null;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  detail: Record<string, unknown>;
  correlation_id: string | null;
}
