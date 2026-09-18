import type { Recommendation, RejectionReason, Role, Stage } from "./types";

export const STAGE_LABEL: Record<Stage, string> = {
  applied: "Applied",
  screened: "Screened",
  interview: "Interview",
  offer: "Offer",
  hired: "Hired",
  rejected: "Rejected",
};

export const FORWARD_STAGES: Stage[] = ["applied", "screened", "interview", "offer", "hired"];

/* Mirrors the server's SLA table, for display only. */
export const STAGE_SLA_DAYS: Partial<Record<Stage, number>> = {
  applied: 3,
  screened: 5,
  interview: 10,
  offer: 5,
};

export const REJECTION_LABEL: Record<RejectionReason, string> = {
  skills_gap: "Skills gap",
  experience_level: "Experience level",
  interview_performance: "Interview performance",
  compensation: "Compensation",
  location: "Location",
  position_filled: "Position filled",
  candidate_withdrew: "Candidate withdrew",
  duplicate: "Duplicate application",
};

export const RECOMMENDATION_LABEL: Record<Recommendation, string> = {
  strong_no: "Strong no",
  no: "No",
  yes: "Yes",
  strong_yes: "Strong yes",
};

export const RATING_LABEL = ["", "Concern", "Mixed", "Solid", "Strong"] as const;

/* Roles whose members can sit on an interview panel (they hold scorecard.submit). */
export const CAN_INTERVIEW: Role[] = ["owner", "recruiter", "hiring_manager", "interviewer"];

export function daysSince(iso: string, now = Date.now()): number {
  return Math.floor((now - new Date(iso).getTime()) / 86_400_000);
}
