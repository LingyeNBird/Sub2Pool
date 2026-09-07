export interface ResearchSummary {
  requests: number;
  gpt6_requests: number;
  other_requests: number;
  raw_usd: number;
  gpt6_raw_usd: number;
  quota_points: number;
  intervals: number;
  groups: number;
  contrasts: number;
  batches: number;
  archived_batches: number;
  quality: Record<string, number>;
  preview: Record<string, unknown> | null;
}
export interface ResearchState {
  enabled: boolean;
  projects: string[];
  endpoint: string;
  interval_hours: number;
  gateway_only: boolean;
  destination_ready: boolean;
  consent_current: boolean;
  policy_version: string;
  last_computed_at: string | null;
  last_sent_at: string | null;
  next_run_at: string | null;
  last_status: string;
  last_error: string;
  summary: ResearchSummary | Record<string, never>;
  method: { method: string; labels: string[]; components: string[] };
  privacy: string[];
  available_projects: Array<{ id: string; title: string }>;
}
