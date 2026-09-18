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

/* -- recruiting ----------------------------------------------------------------- */

export type CriterionKind = "must_have" | "strong_preference" | "nice_to_have";
export type RubricStatus = "draft" | "approved" | "superseded";
export type Verdict = "strong" | "adequate" | "weak" | "absent";
export type AssessmentFlag = "missing_must_have" | "disqualifying_absent" | "low_confidence";

export interface Requisition {
  id: string;
  title: string;
  description: string;
  status: "open" | "closed";
  blind_screening: boolean;
  created_by: string;
  created_at: string;
}

export interface Criterion {
  id: string;
  label: string;
  kind: CriterionKind;
  weight: number;
  evidence_hint: string;
  disqualifying: boolean;
}

export interface CriterionInput {
  label: string;
  kind: CriterionKind;
  weight: number;
  evidence_hint: string;
  disqualifying: boolean;
}

export interface Rubric {
  id: string;
  requisition_id: string;
  version: number;
  status: RubricStatus;
  criteria: Criterion[];
  proposed_by: string;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
}

export interface RequisitionDetail {
  requisition: Requisition;
  rubrics: Rubric[];
}

export interface Candidate {
  id: string;
  name: string;
  email: string | null;
  document_ids: string[];
  created_at: string;
}

export interface Application {
  id: string;
  candidate_id: string;
  requisition_id: string;
  stage: Stage;
  stage_entered_at: string;
  created_at: string;
}

export interface EvidenceRef {
  label: string;
  chunk_id: string;
  document_id: string;
  document_title: string;
  page: number;
  char_start: number;
  char_end: number;
  quoted_text: string;
  rects: Rect[];
}

export interface Judgment {
  criterion_id: string;
  verdict: Verdict;
  confidence: number;
  rationale: string;
  evidence: EvidenceRef[];
}

export interface Assessment {
  id: string;
  application_id: string;
  rubric_id: string;
  rubric_version: number;
  score: number;
  flags: AssessmentFlag[];
  judgments: Judgment[];
  needs_human_review: boolean;
  model_version: string;
  prompt_version: string;
  retrieval_config_hash: string;
  redaction_profile: string;
  assessed_by: string;
  created_at: string;
}

export interface ShortlistEntry {
  application: Application;
  candidate: Candidate;
  assessment: Assessment | null;
  overdue: boolean;
}

/* -- pipeline ------------------------------------------------------------------- */

export type Stage = "applied" | "screened" | "interview" | "offer" | "hired" | "rejected";
export type RejectionReason =
  | "skills_gap"
  | "experience_level"
  | "interview_performance"
  | "compensation"
  | "location"
  | "position_filled"
  | "candidate_withdrew"
  | "duplicate";
export type Recommendation = "strong_no" | "no" | "yes" | "strong_yes";

export interface Scorecard {
  id: string;
  application_id: string;
  interviewer_id: string;
  assigned_by: string;
  assigned_at: string;
  ratings: { criterion_id: string; rating: number; note: string }[];
  recommendation: Recommendation | null;
  summary: string;
  submitted_at: string | null;
  is_submitted: boolean;
}

export interface Transition {
  from_stage: Stage;
  to_stage: Stage;
  by: string;
  at: string;
  reason: RejectionReason | null;
  note: string;
}

export interface ApplicationDetail {
  application: Application;
  candidate: Candidate;
  requisition: Requisition;
  rubric: Rubric | null;
  assessment: Assessment | null;
  transitions: Transition[];
  scorecards: { own: Scorecard | null; others: Scorecard[]; withheld: number; pending: number };
  next_stage: Stage | null;
  next_blockers: string[];
  overdue: boolean;
}

export interface Interview {
  scorecard: Scorecard;
  application: Application;
  candidate: Candidate;
  requisition: Requisition;
}

export interface TalentMatch {
  candidate: Candidate;
  passages: {
    label: string;
    document_id: string;
    document_title: string;
    page: number;
    text: string;
    score: number;
  }[];
}

/* -- content findings ------------------------------------------------------------ */

export type FindingKind =
  | "invisible_text"
  | "low_contrast_text"
  | "micro_text"
  | "off_page_text"
  | "metadata_instruction"
  | "instruction_like_text";

export interface ContentFinding {
  kind: FindingKind;
  text: string;
  page: number | null;
  excluded: boolean;
  instruction_like: boolean;
  rect: Rect | null;
}

/* -- governance ------------------------------------------------------------------ */

export interface ErasureReport {
  candidate_id: string;
  documents_erased: number;
  documents_kept_shared: number;
  files_deleted: number;
  answers_deleted: number;
  events_deleted: number;
  verified: boolean;
  residue: { table: string; column: string; fingerprint: string }[];
}

export interface GroupRate {
  group: string;
  decided: number;
  advanced: number;
  rate: number | null;
  impact_ratio: number | null;
  adverse: boolean;
  too_few: boolean;
}

export interface SelectionReport {
  dimension: string;
  stage: string;
  groups: GroupRate[];
  pending: number;
  rejection_reasons: Record<string, Record<string, number>>;
  four_fifths: number;
  min_group_size: number;
}

export interface RetentionRun {
  retention_days: number;
  dry_run: boolean;
  expired: { candidate_id: string; last_activity: string }[];
  erased: ErasureReport[];
}
