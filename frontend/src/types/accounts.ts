import type { CorrectionBreakdown } from "./common";
export type QuotaProfile = "auto" | "plus" | "pro_5x" | "pro_20x";
export type EffectiveQuotaProfile = Exclude<QuotaProfile, "auto">;
export type AccountProvider = "sub2api" | "cpa";

export interface MonitoredAccount {
  id: number;
  provider: AccountProvider;
  source_account_id: string;
  pool_id: number;
  external_account_id: number | null;
  cpa_auth_index: string | null;
  name: string;
  enabled: boolean;
  quota_query_mode: "passive" | "direct";
  quota_profile: QuotaProfile;
  detected_plan_type: "" | "plus" | "pro";
  effective_quota_profile: EffectiveQuotaProfile;
  capacity_min_usd_override: number | null;
  capacity_max_usd_override: number | null;
  capacity_min_usd: number;
  capacity_max_usd: number;
  last_local_check_at: string | null;
  last_upstream_check_at: string | null;
  last_success_at: string | null;
  next_local_check_at: string | null;
  last_error: string;
}
export interface CPAAccountOption {
  auth_index: string;
  name: string;
  email: string;
  chatgpt_account_id: string;
  plan_type: string;
  status: string;
  status_message: string;
  disabled: boolean;
  unavailable: boolean;
  success: number;
  failed: number;
}
export interface AccountRuntimeStatus {
  name: string | null;
  account_type: string | null;
  status: string | null;
  schedulable: boolean | null;
  current_concurrency: number | null;
  concurrency_limit: number | null;
  last_used_at: string | null;
  rate_limited_at: string | null;
  rate_limit_reset_at: string | null;
  overload_until: string | null;
  temp_unschedulable_until: string | null;
  temp_unschedulable_reason: string | null;
  error_message: string | null;
}
export interface AccountUsageWindow {
  used_percent: number | null;
  reset_at: string | null;
  remaining_seconds: number | null;
  request_count: number | null;
  token_count: number | null;
  account_cost_usd: number | null;
  standard_cost_usd: number | null;
  user_cost_usd: number | null;
}
export interface AccountUsageStatus {
  source: string | null;
  updated_at: string | null;
  five_hour: AccountUsageWindow | null;
  seven_day: AccountUsageWindow | null;
  needs_verify: boolean | null;
  is_banned: boolean | null;
  needs_reauth: boolean | null;
  error_code: string | null;
  error: string | null;
}
export interface AccountUsageStats extends CorrectionBreakdown {
  account_cost_with_correction_usd?: number | null;
  correction_collected_until?: string | null;
  days: number | null;
  actual_days_used: number | null;
  account_cost_usd: number | null;
  fast_correction_usd: number | null;
  account_cost_with_fast_correction_usd: number | null;
  standard_cost_usd: number | null;
  user_cost_usd: number | null;

  request_count: number | null;
  token_count: number | null;
  avg_daily_cost_usd: number | null;
  avg_daily_request_count: number | null;
  avg_daily_token_count: number | null;
  avg_duration_ms: number | null;
  today: {
    date: string | null;
    account_cost_usd: number | null;
    user_cost_usd: number | null;
    request_count: number | null;
    token_count: number | null;
  } | null;
}
export interface AccountCycleUsage {
  sequence: number;
  started_at: string;
  ended_at: string;
  used_percent: number;
  used_usd: number;
  is_current: boolean;
}
export interface AccountStatusAccount {
  cpa_quota?: CPAQuotaDetail;
  id: number;
  provider: AccountProvider;
  source_account_id: string;
  external_account_id: number | null;
  name: string;
  enabled: boolean;
  quota_query_mode: "passive" | "direct";
  cycles: AccountCycleUsage[];
  runtime: AccountRuntimeStatus | null;
  usage: AccountUsageStatus | null;
  stats: AccountUsageStats | null;
  warnings: string[];
}
export interface AccountStatusData {
  configured: boolean;
  sampled_at: string;
  stats_days: number;
  connection_error: string | null;
  accounts: AccountStatusAccount[];
}
export interface OpenAIAccountOption {
  id: number;
  name: string;
  type: string;
  status: string;
  schedulable: boolean;
}

export interface CPAQuotaMetrics {
  request_count: number;
  token_count: number;
  usage_usd: number;
  success_rate?: number | null;
  unpriced_request_count?: number;
}
export interface CPAQuotaPeriod {
  started_at: string;
  ended_at: string;
  metrics: CPAQuotaMetrics;
  coverage_complete: boolean;
  notice?: string;
}
export interface CPAQuotaDetail {
  totals: CPAQuotaPeriod;
  windows: {
    id: string;
    label: string;
    used_percent: number | null;
    reset_at: string | null;
    updated_at: string | null;
    source: string;
    boundary: "unknown" | "provider";
    current: CPAQuotaPeriod | null;
    previous: CPAQuotaPeriod | null;
    prediction: CPAQuotaMetrics | null;
    notice: string | null;
  }[];
  reset: {
    available_count: number | null;
    can_reset: boolean;
    history: {
      id: string;
      status: string;
      created_at: string;
      finished_at: string | null;
    }[];
  };
}
export interface CPAResetPreview {
  id: string;
  account_id: number;
  account_name: string;
  available_count: number;
  status: string;
  expires_at: string;
}
