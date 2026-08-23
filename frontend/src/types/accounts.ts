export interface MonitoredAccount {
  id: number;
  pool_id: number;
  external_account_id: number;
  name: string;
  enabled: boolean;
  quota_query_mode: "passive" | "direct";
  last_local_check_at: string | null;
  last_upstream_check_at: string | null;
  last_success_at: string | null;
  next_local_check_at: string | null;
  last_error: string;
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
export interface AccountUsageStats {
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
export interface AccountStatusAccount {
  id: number;
  external_account_id: number;
  name: string;
  enabled: boolean;
  quota_query_mode: "passive" | "direct";
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
