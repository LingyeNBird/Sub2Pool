import type { CPACollectorStatus } from "./settings";

export interface CPATotals {
  usage_usd: number;
  request_count: number;
  token_count: number;
  unpriced_request_count: number;
}
export interface CPACoverage {
  complete: boolean;
  uncertain_end: boolean;
  gaps: { started_at: string; ended_at: string }[];
}
export interface CPAMember extends CPATotals {
  participant_id: number;
  participant_name: string;
  is_self: boolean;
  is_owner: boolean;
  share_percent: number | null;
  quota_available: boolean;
  is_overused: boolean;
  expected_entitlement_usd: number | null;
  consumed_entitlement_usd: number | null;
  remaining_entitlement_usd: number | null;
  account_breakdowns: {
    account_id: number;
    quota_available: boolean;
    quota_unavailable_reasons: string[];
    quota_as_of: string | null;
    charged_percent: number | null;
    remaining_share_percent: number | null;
    usage_usd: number;
    estimated_capacity_usd: number | null;
    expected_entitlement_usd: number | null;
    consumed_entitlement_usd: number | null;
    remaining_entitlement_usd: number | null;
  }[];
}
export interface CPABillingMember {
  participant_id: number;
  usage_usd: number;
  usage_percent: number | null;
  entitlement_usd: number | null;
  remaining_usd: number | null;
  recommended_usd: number | null;
  recommended_percent: number | null;
  completed_overuse_usd: number | null;
  projected_overuse: boolean | null;
}
export interface CPAWeeklyDistribution {
  account_id: number;
  account_name: string;
  started_at: string;
  resets_at: string | null;
  quota_as_of: string | null;
  requests_as_of: string | null;
  capacity_usd: number | null;
  remaining_usd: number | null;
  upstream_remaining_percent: number | null;
  usage_usd: number;
  unpriced_request_count: number;
  unattributed_usd: number;
  other_members_usd: number;
  members: {
    participant_id: number;
    usage_usd: number;
    usage_percent: number | null;
  }[];
}
export interface CPABillingSummary {
  configured: boolean;
  anchor_date: string | null;
  timezone: string;
  started_at: string | null;
  ended_at: string | null;
  generated_at: string;
  capacity_usd: number | null;
  actual_capacity_usd: number | null;
  future_capacity_usd: number | null;
  expired_usd: number | null;
  available_usd: number | null;
  usage_usd: number;
  unattributed_usd: number;
  other_members_usd: number;
  unallocated_usd: number | null;
  reasons: string[];
  cycles: {
    account_id: number;
    account_name: string;
    started_at: string;
    ended_at: string;
    kind: "historical" | "current" | "future";
    capacity_usd: number | null;
    full_capacity_usd: number | null;
    expired_usd: number | null;
    quota_as_of: string | null;
    reasons: string[];
  }[];
  members: CPABillingMember[];
}
export interface CPAPoolSummary {
  weekly_distribution?: CPAWeeklyDistribution[];
  billing_summary?: CPABillingSummary;
  pool_id: number;
  pool_name: string;
  selected_account_id: number;
  partial_scope: boolean;
  accounts: (CPATotals & {
    account_id: number;
    account_name: string;
    owner: {
      participant_id: number | null;
      participant_name: string | null;
      started_at: string | null;
      status: "active" | "missing" | "ambiguous";
    };
    selected: boolean;
    upstream_used_percent?: number | null;
    quota_as_of: string | null;
    requests_as_of: string | null;
    cycle_started_at: string;
    resets_at: string | null;
    coverage: CPACoverage;
    quota_available: boolean;
    quota_unavailable_reasons: string[];
  })[];
  members: CPAMember[];
  unattributed: CPATotals;
  collector: CPACollectorStatus;
  cost_estimate: boolean;
  enforcement_enabled: boolean;
  generated_at: string;
}
export interface CPABinding {
  id: number;
  key_id: number;
  hint: string;
  name: string;
  participant_id: number;
  participant_name: string;
  started_at: string;
  ended_at: string | null;
}
export interface CPAKeys {
  keys: {
    id: number;
    name: string;
    hint: string;
    observed_hash: string;
    bindings: CPABinding[];
  }[];
  unregistered: { observed_hash: string; hint: string }[];
}
export interface CPAClaim {
  id: string;
  key_id: number | null;
  account_id?: number | null;
  participant_id: number;
  started_at: string;
  ended_at: string;
  expires_at: string;
  applied_at: string | null;
  historical_contract_policy: string;
  accounts: (CPATotals & {
    account_id: number;
    account_name: string;
    coverage: CPACoverage;
  })[];
}
export interface CPARequest {
  id: number;
  occurred_at: string;
  request_id: string;
  api_key_hint: string;
  api_key_alias?: string;
  model: string;
  endpoint: string;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  failed: boolean;
  latency_ms: number;
  ttft_ms: number;
  usage_usd: number;
  unpriced: boolean;
  requested_service_tier: string;
  response_service_tier: string;
}
export interface CPARequestSummary {
  request_count: number;
  failed_count: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  unpriced_request_count: number;
  usage_usd: number;
  average_latency_ms: number | null;
  average_ttft_ms: number | null;
}
export interface CPARequests {
  summary: CPARequestSummary | null;
  started_at: string;
  ended_at: string;
  account_id: number;
  items: CPARequest[];
  total: number;
  page: number;
  page_size: number;
  keys: { id: number; name: string; hint: string }[];
  models: string[];
  generated_at: string;
}
