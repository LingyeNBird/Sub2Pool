import type { MonitoredAccount } from "./accounts";
import type { CostBreakdown } from "./common";
import type { Participant } from "./participants";

export interface ModelDiagnostics {
  algorithm: string;
  seed: number;
  particles: number;
  quantizer_probabilities: Record<string, number>;
  speed_probabilities: Record<string, number>;
  ess_fraction: number;
  resampled: boolean;
  progress_probability_interval: [number, number];
  progress_deterministic_bounds: [number, number];
  deterministic_repairs: number;
  residual_cost_usd: number;
  aggregate_cost_difference_usd: number;
  prior_capacity_usd: number | null;
  capacity_range_usd: [number, number];
  capacity_range_stage: number;
  capacity_range_direction: "upper" | "lower" | null;
  capacity_range_promotions: Array<{
    stage: number;
    direction: "upper" | "lower";
    model_row: number;
    model_time_hours: number;
    from_range_usd: [number, number];
    to_range_usd: [number, number];
    boundary_mass: number;
    display_residual_pp: number;
  }>;
  boundary_mass: {
    lower: number;
    upper: number;
  };
}
export interface DashboardData {
  configured: boolean;
  monitoring_enabled: boolean;
  accounts: MonitoredAccount[];
  selected_account_id: number | null;
  selected_provider: "sub2api" | "cpa" | null;
  last_local_check_at: string | null;
  last_upstream_check_at: string | null;
  snapshot_stale: boolean;
  last_success_at: string | null;
  last_error: string;
  sub2api_admin_url: string;
  upstream_admin_url: string;
  fast_correction_enabled: boolean;
  quota_query_mode: "passive" | "direct" | null;
  weekly_quota_model: "time_varying" | "constant_average";
  needs_manual_update_count: number;
  cycle: null | {
    id: number;
    observed_at: string;
    starts_at: string;
    resets_at: string;
    upstream_used_percent: number | null;
    interval_used_percent: number;
    effective_usd_per_percent: number | null;
    selected_total_cost: number | null;
    selected_total_cost_breakdown: CostBreakdown;
    start_cost_breakdown: CostBreakdown;
    unattributed_used_percent: number | null;
    sample_note: string;
    snapshot_sampled_at: string | null;
    rate_calculated: boolean;
    estimated_used_percent: number;
    capacity_lower_usd: number | null;
    capacity_upper_usd: number | null;
    model_diagnostics: ModelDiagnostics | Record<string, never>;
  };
  participants: Participant[];
}
