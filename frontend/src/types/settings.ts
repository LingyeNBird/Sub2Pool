export interface FastCorrectionRule {
  model_pattern: string;
  source_multiplier: string | number;
  target_multiplier: string | number;
}
export interface CPAModelPrice {
  input: string | number;
  cached_input: string | number;
  output: string | number;
}
export type CPAModelPricing = Record<string, CPAModelPrice>;
export interface CPACollectorStatus {
  state: "connected" | "stale" | "error" | "idle";
  connected: boolean;
  stale: boolean;
  connected_at: string | null;
  heartbeat_at: string | null;
  last_message_at: string | null;
  last_persisted_at: string | null;
  pending_count: number;
  last_error: string;
  last_error_at: string | null;
}

export interface AppSettingsData {
  [key: string]:
    | string
    | number
    | boolean
    | null
    | FastCorrectionRule[]
    | CPAModelPricing
    | CPACollectorStatus;
  monitoring_enabled: boolean;
  sub2api_base_url: string;
  cpa_base_url: string;
  cpa_management_key_configured: boolean;
  cpa_fast_multiplier: number;
  cpa_double_billing_enabled: boolean;
  cpa_double_billing_threshold_tokens: number;
  cpa_double_billing_multiplier: number;
  cpa_model_pricing: CPAModelPricing;
  cpa_collector_status: CPACollectorStatus;
  request_timeout_seconds: number;
  verify_tls: boolean;
  timezone: string;
  cost_basis: string;
  weekly_quota_model: "time_varying" | "constant_average";
  fast_correction_enabled: boolean;
  fast_correction_rules: FastCorrectionRule[];
  fast_correction_rebuild_recommended: boolean;
  fast_correction_missing_intervals: number;
  initial_usd_per_percent: number;
  safety_factor: number;
  daily_estimate_min_percent_span: number;
  local_poll_minutes: number;
  progress_threshold_percent: number;
  active_max_calibration_hours: number;
  reset_proximity_minutes: number;
  stale_warning_hours: number;
  limit_warning_usd: number;
  recommendation_change_usd: number;
  rate_change_alert_percent: number;
  notify_on_limit_exhausted: boolean;
  notify_on_recommendation_change: boolean;
  email_provider: "smtp" | "resend";
  notify_on_rate_change: boolean;
  notify_on_collection_error: boolean;
  notification_cooldown_minutes: number;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_use_tls: boolean;
  smtp_use_ssl: boolean;
  smtp_from_email: string;
  notification_email: string;
  resend_from_email: string;
  resend_api_key_configured: boolean;
  sub2api_token_configured: boolean;
  smtp_password_configured: boolean;
  readonly_api_key_configured: boolean;
  readonly_api_key_hint: string;
  readonly_api_key_created_at: string | null;
  last_local_check_at: string | null;
  last_upstream_check_at: string | null;
  last_success_at: string | null;
  last_error: string;
}
export interface APIKeyState {
  configured: boolean;
  hint: string;
  created_at: string | null;
}
export interface ReadOnlyAPIKeyGenerated {
  api_key: string;
  hint: string;
  created_at: string;
}
export interface HistoricalRebuildBlocker {
  code: string;
  severity: "hard" | "warning";
  point_id: number | null;
  message: string;
}
export interface HistoricalReplaySummary {
  rebuilt_observations?: number;
  automatic_exclusions?: number;
  inferred_intervals?: number;
  latest_observation_id?: number | null;
}
export interface HistoricalRebuildPlan {
  id: string;
  account_id: number;
  state:
    | "generating"
    | "ready"
    | "blocked"
    | "stale"
    | "applying"
    | "applied"
    | "failed";
  digest: string;
  created_at: string;
  expires_at: string;
  base_revision: number;
  result_revision: number | null;
  blockers: HistoricalRebuildBlocker[];
  replay_summary: HistoricalReplaySummary;
  safe_to_apply: boolean;
  algorithm_version: string;
  build_id: string;
}
