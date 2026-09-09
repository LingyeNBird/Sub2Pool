export interface BurstMember {
  participant_id: number;
  user_id: number;
  name: string;
  base_share: string;
  opening_adjustment: string;
  effective_share?: string;
  used_percent?: string;
  next_adjustment?: string;
}
export interface BurstCycle {
  cycle_id: number;
  account_id: number;
  account_name: string;
  resets_at: string;
  is_burst_cycle: boolean;
  settled_at: string | null;
  evidence_at: string | null;
  error: string;
  carry_edits?: {
    participant_id: number;
    user_id: number;
    before: string;
    after: string;
    edited_at: string;
    admin_id: number;
    admin_username: string;
  }[];
  settlement_context?: {
    remaining_percent?: string;
    eligible: boolean;
    quota_observed_at?: string;
    seconds_before_reset?: number;
    reason: string;
  };
  members: BurstMember[];
  settlement: BurstMember[];
}
export interface TemporaryBurstData {
  carryover_enabled: boolean | null;
  terminated_at: string | null;
  can_stop: boolean;
  active: boolean;
  session_id: number | null;
  started_at: string | null;
  expires_at: string | null;
  ended_at: string | null;
  auto_apply: boolean;
  monitoring_enabled: boolean;
  recommended_balance_usd: number;
  can_start: boolean;
  reminder_enabled: boolean;
  reminder_email_ready: boolean;
  enabled_account_count: number;
  sampling: {
    account_id: number;
    account_name: string;
    interval_seconds: number;
    accelerated: boolean;
  }[];
  cycles: BurstCycle[];
  application?: { applied: number; failed: number };
}
