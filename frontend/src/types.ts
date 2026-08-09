export interface Snapshot {
  participant_id: number;
  participant_name: string;
  selected_cost: number;
  delta_cost: number | null;
  charged_delta_percent: number;
  charged_cycle_percent: number;
  remaining_share_percent: number;
  current_balance_usd: number | null;
  recommended_balance_usd: number | null;
  recommended_balance_min_usd: number | null;
  recommended_balance_max_usd: number | null;
  balance_difference_usd: number | null;
  needs_manual_update: boolean;
  recommendation_applied: boolean;
  reason: string;
  allocation_model: "time_varying" | "constant_average";
}

export interface Participant {
  id: number;
  name: string;
  email: string;
  sub2api_user_id: number;
  sub2api_username: string;
  sub2api_email: string;
  sub2api_identity: string;
  share_percent: number;
  is_owner: boolean;
  enabled: boolean;
  notes: string;
  latest_balance_usd: number | null;
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

export interface PaginationMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface PaginatedData<T> {
  items: T[];
  pagination: PaginationMeta;
}

export interface SystemUser {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  participant_ids: number[];
  participant_names: string[];
  last_login: string | null;
  date_joined: string;
}
export interface CostBreakdown {
  sub2api_cost_usd: number;
  fast_correction_usd: number;
  total_cost_usd: number;
}

export interface RateSample {
  observed_at: string;
  cost_usd: number;
  cost_breakdown: CostBreakdown;
  used_percent: number;
  usd_per_percent: number;
}
export interface DashboardData {
  configured: boolean;
  monitoring_enabled: boolean;
  last_local_check_at: string | null;
  last_upstream_check_at: string | null;
  snapshot_stale: boolean;
  last_success_at: string | null;
  last_error: string;
  sub2api_admin_url: string;
  fast_correction_enabled: boolean;
  quota_query_mode: string;
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
    conservative_percentile: number;
    rate_history_samples: number;
    rate_sample_count: number;
    rate_samples: RateSample[];
  };
  participants: Participant[];
}

export interface Observation {
  id: number;
  observed_at: string;
  source: string;
  account_id: number;
  attribution_started_at: string | null;
  upstream_resets_at: string;
  upstream_used_percent: number;
  interval_used_percent: number;
  raw_selected_total_cost: number;
  selected_total_cost: number;
  delta_percent: number | null;
  delta_cost: number | null;
  sample_usd_per_percent: number | null;
  effective_usd_per_percent: number;
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
}

export interface FastCorrectionUserDetail {
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

export interface FastCorrectionDetail {
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
  sub2api_fast_multiplier: number;
  upstream_fast_multiplier: number;
  correction_ratio: number;
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

export interface FastCorrectionRebuildResult {
  scope: "cycle" | "all";
  rebuilt_observations: number;
  request_count: number;
  fast_request_count: number;
  correction_usd: number;
  replay_started_at: string | null;
  replayed_observations: number;
}

export interface MonitorSchedule {
  monitoring_enabled: boolean;
  interval_seconds: number;
  next_local_check_at: string | null;
  run_in_progress: boolean;
  server_time: string;
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

export interface LoginEventData extends PaginatedData<LoginEventRecord> {
  success_count: number;
  failure_count: number;
  unique_request_ips: number;
}

export interface ObservationListData extends PaginatedData<Observation> {
  fast_correction_enabled: boolean;
  summary: {
    total: number;
    valid_count: number;
    passive_count: number;
    excluded_count: number;
  };
}

export interface SelectOption {
  value: string;
  label: string;
}

export interface NotificationListData extends PaginatedData<NotificationRecord> {
  summary: {
    total: number;
    sent_count: number;
    failed_count: number;
  };
  filter_options: {
    types: SelectOption[];
    participants: { id: number; name: string }[];
    statuses: SelectOption[];
  };
}

export type BlockedIPSource = "request" | "remote" | "webrtc";

export interface BlockedIPAddress {
  id: number;
  address: string;
  source_type: BlockedIPSource;
  source_label: string;
  notes: string;
  login_event_id: number | null;
  created_at: string;
}

export interface CapacityClosingBasis {
  observed_at: string;
  starts_at: string | null;
  start_cost_usd: number;
  start_percent: number;
  start_cost_breakdown: CostBreakdown;
  end_cost_usd: number;
  end_percent: number;
  end_cost_breakdown: CostBreakdown;
  raw_estimate_usd: number | null;
  estimate_usd: number;
  effective_usd_per_percent: number;
  calculation_model: "time_varying" | "constant_average";
  rate_source: string;
  sample_note: string;
  conservative_percentile: number;
  rate_history_samples: number;
  rate_sample_count: number;
  rate_samples: RateSample[];
}

export interface CapacityDailyClosingBasis {
  observed_from: string;
  observed_to: string;
  start_cost_usd: number;
  start_cost_breakdown: CostBreakdown;
  start_percent: number;
  end_cost_usd: number;
  end_cost_breakdown: CostBreakdown;
  end_percent: number;
  cost_delta_usd: number;
  percent_delta: number;
  estimate_usd: number;
  minimum_usd: number;
  maximum_usd: number | null;
  sample_count: number;
  min_percent_span: number;
}

export interface CapacityPoint {
  period: string;
  weekly_total_usd: number;
  minimum_usd: number;
  maximum_usd: number;
  sample_count: number;
  basis: CapacityClosingBasis | null;
  daily_total_usd: number | null;
  daily_basis: CapacityDailyClosingBasis | null;
}

export interface CycleCapacityEstimate {
  estimate_usd: number;
  raw_estimate_usd: number | null;
  start_cost_usd: number;
  start_cost_breakdown: CostBreakdown;
  start_percent: number;
  end_cost_usd: number;
  end_cost_breakdown: CostBreakdown;
  end_percent: number;
  cost_usd: number;
  used_percent: number;
  effective_usd_per_percent: number;
  calculation_model: "time_varying" | "constant_average";
  rate_calculated: boolean;
  conservative_percentile: number;
  rate_history_samples: number;
  rate_sample_count: number;
  rate_samples: RateSample[];
  confidence: "低" | "中" | "高";
  observed_at: string;
  starts_at: string;
  resets_at: string;
}

export interface DailyCapacityEstimate {
  estimate_usd: number | null;
  minimum_usd: number | null;
  maximum_usd: number | null;
  start_cost_usd: number | null;
  start_cost_breakdown: CostBreakdown | null;
  start_percent: number | null;
  end_cost_usd: number | null;
  end_cost_breakdown: CostBreakdown | null;
  end_percent: number | null;
  cost_delta_usd: number | null;
  percent_delta: number | null;
  sample_count: number;
  observed_from: string | null;
  observed_to: string | null;
  min_percent_span: number;
  sufficient: boolean;
  reason: string;
}

export interface CapacitySummary {
  cycle: CycleCapacityEstimate | null;
  today: DailyCapacityEstimate;
}

export interface UsagePoint {
  observed_at: string;
  label: string;
  account_cycle_usage_usd: number;
  balance_usd: number | null;
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
  fast_correction_enabled: boolean;
  capacity_summary: CapacitySummary;
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
  weekly_quota_model: "time_varying" | "constant_average";
  fast_correction_enabled: boolean;
  fast_correction_rebuild_recommended: boolean;
  fast_correction_missing_intervals: number;
  initial_usd_per_percent: number;
  safety_factor: number;
  conservative_percentile: number;
  rate_history_samples: number;
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
  last_local_check_at: string | null;
  last_upstream_check_at: string | null;
  last_success_at: string | null;
  last_error: string;
}
