export interface ResearchSummary {
  window_days: number;
  requests: number;
  baseline_requests: number;
  gpt6_requests: number;
  raw_usd: number;
  gpt6_raw_usd: number;
  quota_points: number;
  cycles: number;
  blocks: number;
  eligible: boolean;
  status: string;
  gateway_only: boolean;
  design_rank: number;
  identifiable: boolean[];
  exclusions: Record<string, number>;
  support: number[];
  score_mean: number[];
  score_cov: number[][];
  factor_estimates: number[][];
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
  can_withdraw: boolean;
  last_sent_endpoint: string;
  summary: ResearchSummary | Record<string, never>;
  method: { method: string; labels: string[]; components: string[] };
  privacy: string[];
  available_projects: Array<{ id: string; title: string }>;
}
