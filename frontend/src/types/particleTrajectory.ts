import type { MonitoredAccount } from "./accounts";

export interface ParticleTrajectoryPoint {
  observation_id: number;
  observed_at: string;
  source: string;
  displayed_percent: number;
  estimated_percent: number;
  estimated_percent_lower: number;
  estimated_percent_upper: number;
  capacity_usd: number;
  capacity_lower_usd: number;
  capacity_upper_usd: number;
  range_min_usd: number;
  range_max_usd: number;
  range_stage: number;
  range_direction: "upper" | "lower" | null;
  ess_fraction: number;
  resampled: boolean;
  boundary_mass: {
    lower: number;
    upper: number;
  };
  particles_usd: number[];
}
export interface ParticleRangePromotion {
  stage: number;
  direction: "upper" | "lower";
  occurred_at: string;
  from_range_usd: [number, number];
  to_range_usd: [number, number];
  boundary_mass: number;
  display_residual_pp: number;
}
export interface ParticleTrajectoryPeriod {
  id: number;
  sequence: number;
  started_at: string;
  first_observed_at: string;
  last_observed_at: string;
  resets_at: string;
  ended_at: string;
  observation_count: number;
  is_current: boolean;
}
export interface ParticleTrajectoryData {
  account?: Pick<MonitoredAccount, "id" | "external_account_id" | "name">;
  available: boolean;
  message: string;
  algorithm?: string;
  seed?: number;
  particle_count?: number;
  representative_particle_count?: number;
  credible_mass_percent?: number;
  selected_period_id?: number;
  periods?: ParticleTrajectoryPeriod[];
  segment?: {
    started_at: string;
    first_observed_at: string;
    resets_at: string;
    reason: string;
    reason_label: string;
    observation_count: number;
  };
  latest?: {
    observed_at: string;
    capacity_usd: number;
    capacity_lower_usd: number;
    capacity_upper_usd: number;
    range_min_usd: number;
    range_max_usd: number;
    range_stage: number;
    ess_fraction: number;
  };
  points?: ParticleTrajectoryPoint[];
  promotions?: ParticleRangePromotion[];
}
