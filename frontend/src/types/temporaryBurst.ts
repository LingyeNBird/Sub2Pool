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
  account_id: number;
  account_name: string;
  resets_at: string;
  is_burst_cycle: boolean;
  settled_at: string | null;
  evidence_at: string | null;
  error: string;
  members: BurstMember[];
  settlement: BurstMember[];
}
export interface TemporaryBurstData {
  active: boolean;
  session_id: number | null;
  started_at: string | null;
  expires_at: string | null;
  ended_at: string | null;
  auto_apply: boolean;
  monitoring_enabled: boolean;
  recommended_balance_usd: number;
  can_start: boolean;
  cycles: BurstCycle[];
  application?: { applied: number; failed: number };
}
