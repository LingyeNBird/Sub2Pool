import type { MonitoredAccount } from "./accounts";
import type { CorrectionBreakdown, PaginatedData } from "./common";
import type { ModelDiagnostics } from "./dashboard";
import type { Snapshot } from "./participants";

export interface Observation extends CorrectionBreakdown {
  id: number;
  observed_at: string;
  source: string;
  provider: "sub2api" | "cpa";
  account_id: number;
  attribution_started_at: string | null;
  upstream_resets_at: string;
  upstream_used_percent: number;
  interval_used_percent: number;
  raw_selected_total_cost: number;
  selected_total_cost: number;
  cost_window_started_at: string | null;
  cost_window_ended_at: string | null;
  interval_cost_started_at: string | null;
  interval_cost: number | null;
  interval_cost_source: string;
  normalized_total_cost: number;
  delta_percent: number | null;
  delta_cost: number | null;
  sample_usd_per_percent: number | null;
  effective_usd_per_percent: number;
  estimated_used_percent: number;
  capacity_lower_usd: number | null;
  capacity_upper_usd: number | null;
  model_diagnostics: ModelDiagnostics | Record<string, never>;
  fast_correction_usd: number | null;
  fast_correction_calculated: boolean;
  valid_sample: boolean;
  sample_note: string;
  rate_method: string;
  query_mode: string;
  snapshot_sampled_at: string | null;
  participants: Snapshot[];
  excluded: boolean;
  excluded_at: string | null;
  exclusion_reason: string;
  exclusion_source: "" | "manual" | "automatic";
  is_manual_start: boolean;
  manual_start_reason: string;
  manual_start_set_at: string | null;
  manual_start_end_id: number | null;
  manual_start_end_observed_at: string | null;
}
export interface FastCorrectionUserDetail extends CorrectionBreakdown {
  raw_cost_usd?: number | null;
  corrected_cost_usd?: number | null;
  sub2api_user_id: number;
  username: string;
  email: string;
  display_name: string;
  request_count: number | null;
  fast_request_count: number;
  non_fast_request_count: number | null;
  fast_billed_cost_usd: number;
  correction_usd: number;
  corrected_fast_cost_usd: number;
}
export interface FastCorrectionCalculateResult extends CorrectionBreakdown {
  observation_id: number;
  fast_correction_usd: number;
  fast_correction_calculated: boolean;
  correction_calculated?: boolean;
}
export interface FastCorrectionDetail extends CorrectionBreakdown {
  raw_cost_usd?: number | null;
  corrected_cost_usd?: number | null;
  rules_digest?: string;
  rules?: Record<string, unknown>;
  model_details?: CorrectionModelDetail[];
  observation_id: number;
  started_at: string | null;
  ended_at: string;
  calculated: boolean;
  cost_basis: "actual" | "standard";
  cost_basis_label: string;
  request_count: number | null;
  fast_request_count: number;
  non_fast_request_count: number | null;
  fast_billed_cost_usd: number;
  correction_usd: number;
  corrected_fast_cost_usd: number;
  collection_error: string;
  users: FastCorrectionUserDetail[];
}
export interface ObservationRebuildResult {
  rebuilt_observations: number;
  automatic_exclusions: number;
  inferred_intervals: number;
  latest_observation_id: number | null;
  replay_started_at: string | null;
}
export interface MonitorSchedule {
  monitoring_enabled: boolean;
  interval_seconds: number;
  next_local_check_at: string | null;
  run_in_progress: boolean;
  accounts: Array<{
    id: number;
    provider: "sub2api" | "cpa";
    source_account_id: string;
    external_account_id: number | null;
    name: string;
    enabled: boolean;
    next_local_check_at: string | null;
    run_in_progress: boolean;
  }>;
  server_time: string;
}
export interface ObservationListData extends PaginatedData<Observation> {
  account: Pick<
    MonitoredAccount,
    "id" | "provider" | "source_account_id" | "external_account_id" | "name"
  > | null;
  fast_correction_enabled: boolean;
  corrections_available?: boolean;
  summary: {
    total: number;
    valid_count: number;
    passive_count: number;
    excluded_count: number;
  };
}

export interface CorrectionModelDetail extends CorrectionBreakdown {
  model: string;
  service_tier: string;
  request_count: number;
  raw_cost_usd: number;
  corrected_cost_usd: number;
  fast_factor: string;
  long_context_factor: string;
  model_factor: string;
  long_context_evidence: string;
}
