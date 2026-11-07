// types.ts — typed API contracts shared by dashboard, live workspace, and report views.
export type Status =
  | "planning"
  | "awaiting_approval"
  | "running"
  | "reporting"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";
export interface Step {
  number: number;
  task: string;
  details: string;
  detail: string;
  status: "pending" | "in_progress" | "completed" | "failed" | "skipped";
}
export interface Evidence {
  id: number;
  url: string;
  title: string;
  quote: string;
  retrieved_at: string;
}
export interface Claim {
  text: string;
  evidence_ids: number[];
}
export interface Report {
  title: string;
  summary: Claim[];
  findings: Claim[];
  comparisons: { criterion: string; observations: Claim[] }[];
  missing_information: string[];
  completion_status: "completed" | "partial";
}
export interface AuditEvent {
  id: number;
  created_at: string;
  kind: string;
  message: string;
  action?: { command: string };
  url?: string;
}
export interface Session {
  id: string;
  title: string;
  prompt: string;
  objective: string;
  status: Status;
  message: string;
  steps: Step[];
  evidence: Evidence[];
  report: Report | null;
  error: string | null;
  active_url: string;
  page_title: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  action_count: number;
  screenshot_version: number;
  revision: number;
  version: number;
  usage: { calls: number; input_tokens: number; output_tokens: number };
  events: AuditEvent[];
}
export type Recent = Pick<
  Session,
  | "id"
  | "title"
  | "prompt"
  | "status"
  | "created_at"
  | "completed_at"
  | "action_count"
>;
export interface Config {
  configured: boolean;
  model: string;
  max_steps: number;
  max_runtime_seconds: number;
  max_concurrent_sessions: number;
  max_session_tokens: number;
  access_protected: boolean;
}
