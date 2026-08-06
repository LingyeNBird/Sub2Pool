export interface Snapshot {
  participant_id: number;
  participant_name: string;
  selected_cost: number;
  delta_cost: number | null;
  charged_delta_percent: number;
  charged_cycle_percent: number;
  remaining_share_percent: number;
  platform_weekly_usage_usd: number | null;
  platform_weekly_limit_usd: number | null;
  recommended_weekly_limit_usd: number;
  recommendation_difference_usd: number | null;
  needs_manual_update: boolean;
  reason: string;
}

export interface Participant {
  id: number;
  name: string;
  email: string;
  sub2api_user_id: number;
  share_percent: number;
  is_owner: boolean;
  enabled: boolean;
  notes: string;
  latest_weekly_usage_usd: number | null;
  latest_weekly_limit_usd: number | null;
  latest_selected_cost: number | null;
  last_checked_at: string | null;
  snapshot: Snapshot | null;
}

export interface Sub2APIUserOption {
  id: number;
  email: string;
  username: string;
  status: string;
  role: string;
}

export interface DashboardData {
  configured: boolean;
  monitoring_enabled: boolean;
  last_local_check_at: string | null;
  last_upstream_check_at: string | null;
  last_success_at: string | null;
  last_error: string;
  quota_query_mode: string;
  needs_manual_update_count: number;
  cycle: null | {
    id: number;
    starts_at: string;
    resets_at: string;
    upstream_used_percent: number | null;
    effective_usd_per_percent: number | null;
    selected_total_cost: number | null;
    unattributed_used_percent: number | null;
    sample_note: string;
    snapshot_sampled_at: string | null;
  };
  participants: Participant[];
}

export interface Observation {
  id: number;
  observed_at: string;
  source: string;
  cycle_id: number;
  cycle_resets_at: string;
  upstream_used_percent: number;
  selected_total_cost: number;
  delta_percent: number | null;
  delta_cost: number | null;
  sample_usd_per_percent: number | null;
  effective_usd_per_percent: number;
  valid_sample: boolean;
  sample_note: string;
  query_mode: string;
  snapshot_sampled_at: string | null;
  participants: Snapshot[];
}

export interface NotificationRecord {
  id: number;
  event_type: string;
  event_type_label: string;
  severity: string;
  participant_name: string | null;
  recipient: string;
  subject: string;
  body: string;
  status: string;
  status_label: string;
  error: string;
  created_at: string;
  sent_at: string | null;
}

export interface LoginEventRecord {
  id: number;
  username: string;
  success: boolean;
  request_ip: string | null;
  remote_ip: string | null;
  webrtc_supported: boolean | null;
  webrtc_ips: string[];
  user_agent: string;
  failure_reason: string;
  created_at: string;
}

export interface LoginEventData {
  success_count: number;
  failure_count: number;
  unique_request_ips: number;
  items: LoginEventRecord[];
}

export interface CapacityPoint {
  period: string;
  weekly_total_usd: number;
  minimum_usd: number;
  maximum_usd: number;
  sample_count: number;
}

export interface UsagePoint {
  observed_at: string;
  label: string;
  weekly_usage_usd: number;
  weekly_limit_usd: number | null;
}

export interface ParticipantUsageSeries {
  participant_id: number;
  participant_name: string;
  sub2api_user_id: number;
  points: UsagePoint[];
}

export interface StatisticsData {
  capacity_period: "day" | "month";
  capacity_series: CapacityPoint[];
  usage_days: number;
  usage_precision: "raw" | "hour" | "day";
  sample_interval_minutes: number;
  participant_series: ParticipantUsageSeries[];
}

export interface OpenAIAccountOption {
  id: number;
  name: string;
  type: string;
  status: string;
  schedulable: boolean;
}

export interface AppSettingsData {
  [key: string]: string | number | boolean | null;
  monitoring_enabled: boolean;
  sub2api_base_url: string;
  openai_account_id: number | null;
  quota_query_mode: string;
  request_timeout_seconds: number;
  verify_tls: boolean;
  timezone: string;
  cost_basis: string;
  initial_usd_per_percent: number;
  safety_factor: number;
  conservative_percentile: number;
  rate_history_samples: number;
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
  last_local_check_at: string | null;
  last_upstream_check_at: string | null;
  last_success_at: string | null;
  last_error: string;
}
