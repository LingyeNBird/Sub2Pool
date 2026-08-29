import type { MonitoredAccount } from "./accounts";
import type { CostBreakdown } from "./common";

export interface CapacityClosingBasis {
  observed_at: string;
  starts_at: string | null;
  start_cost_usd: number;
  start_percent: number;
  start_cost_breakdown: CostBreakdown;
  end_cost_usd: number;
  end_cost_breakdown: CostBreakdown;
  end_percent: number;
  raw_estimate_usd: number | null;
  estimate_usd: number | null;
  effective_usd_per_percent: number | null;
  calculation_model: "endpoint_ratio";
  rate_source: string;
  sample_note: string;
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
  estimate_usd: number | null;
  raw_estimate_usd: number | null;
  start_cost_usd: number;
  start_cost_breakdown: CostBreakdown;
  start_percent: number;
  end_cost_usd: number;
  end_cost_breakdown: CostBreakdown;
  end_percent: number;
  cost_usd: number;
  used_percent: number;
  effective_usd_per_percent: number | null;
  calculation_model: "endpoint_ratio";
  rate_calculated: boolean;
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
  account_id: number;
  external_account_id: number;
  sub2api_user_id: number;
  points: UsagePoint[];
}
export interface CPAAPIKeyUsagePoint {
  observed_at: string;
  label: string;
  usage_usd: number;
  request_count: number;
  token_count: number;
}
export interface CPAAPIKeyUsageSeries {
  api_key_id: string;
  api_key_name: string;
  total_usage_usd: number;
  request_count: number;
  token_count: number;
  unpriced_request_count: number;
  points: CPAAPIKeyUsagePoint[];
}
export interface APIKeyUsageItem {
  api_key_id: number | null;
  name: string;
  status: string;
  usage_usd: number;
  participant_usage_percent: number;
  weekly_quota_percent: number;
}
export interface APIUsageBreakdown {
  participant_id: number;
  participant_name: string;
  sub2api_user_id: number;
  starts_at: string;
  observed_to: string;
  cost_basis: "actual" | "standard";
  fast_correction_enabled: boolean;
  participant_total_usd: number;
  weekly_total_estimate_usd: number | null;
  participant_weekly_percent: number;
  api_keys: APIKeyUsageItem[];
}
export interface StatisticsData {
  account: Pick<
    MonitoredAccount,
    "id" | "provider" | "source_account_id" | "external_account_id" | "name"
  >;
  capacity_period: "day" | "month";
  capacity_series: CapacityPoint[];
  fast_correction_enabled: boolean;
  capacity_summary: CapacitySummary;
  usage_days: number;
  usage_precision: "raw" | "hour" | "day";
  sample_interval_minutes: number;
  participant_series: ParticipantUsageSeries[];
  cpa_api_key_series: CPAAPIKeyUsageSeries[];
}
