import type {
  ModelDiagnostics,
  Observation,
  Participant,
  ParticleTrajectoryPoint,
  Snapshot,
} from "@/types";

import type { DemoPeriod } from "./state";
import { rounded, snapshot } from "./participantProjection";

export const DEMO_ANCHOR = Date.UTC(2026, 7, 12, 4, 0, 0);
export const HOUR = 3_600_000;
export const DAY = HOUR * 24;

export function iso(timestamp: number): string {
  return new Date(timestamp).toISOString();
}

function hash(seed: number): number {
  let value = seed | 0;
  value ^= value << 13;
  value ^= value >>> 17;
  value ^= value << 5;
  return ((value >>> 0) % 10_000) / 10_000;
}

function signedNoise(seed: number): number {
  return hash(seed) * 2 - 1;
}

function diagnostics(
  seed: number,
  estimatedPercent: number,
  capacity: number,
  lower: number,
  upper: number,
): ModelDiagnostics {
  return {
    algorithm: "time_varying_particle_filter_v2_demo",
    seed,
    particles: 1600,
    quantizer_probabilities: { round: 0.61, floor: 0.25, ceil: 0.14 },
    speed_probabilities: { stable: 0.72, rising: 0.22, falling: 0.06 },
    ess_fraction: rounded(0.54 + hash(seed) * 0.35, 4),
    resampled: seed % 9 === 0,
    progress_probability_interval: [
      rounded(Math.max(0, estimatedPercent - 1.25), 4),
      rounded(Math.min(100, estimatedPercent + 1.4), 4),
    ],
    progress_deterministic_bounds: [
      Math.max(0, Math.floor(estimatedPercent)),
      Math.min(100, Math.ceil(estimatedPercent + 1)),
    ],
    deterministic_repairs: seed % 7 === 0 ? 1 : 0,
    residual_cost_usd: rounded(1.5 + hash(seed + 3) * 7, 5),
    aggregate_cost_difference_usd: rounded(hash(seed + 5) * 0.00001, 7),
    prior_capacity_usd: rounded(capacity * 0.97, 2),
    capacity_range_usd: [lower, upper],
    capacity_range_stage: seed % 31 === 0 ? 1 : 0,
    capacity_range_direction: seed % 31 === 0 ? "upper" : null,
    capacity_range_promotions:
      seed % 31 === 0
        ? [
            {
              stage: 1,
              direction: "upper",
              model_row: seed,
              model_time_hours: rounded(seed / 6, 2),
              from_range_usd: [lower - 180, upper - 120],
              to_range_usd: [lower, upper],
              boundary_mass: 0.082,
              display_residual_pp: 0.41,
            },
          ]
        : [],
    boundary_mass: { lower: 0.022, upper: 0.037 },
  };
}

function participantSnapshots(
  participants: Participant[],
  cycleCost: number,
  usedPercent: number,
): Snapshot[] {
  return participants.map((participant, index) => {
    const allocation = participant.pool_allocations[0];
    return snapshot(
      participant,
      allocation?.share_percent ?? 0,
      cycleCost,
      usedPercent,
      index,
    );
  });
}

function buildPeriods(participants: Participant[]): {
  periods: DemoPeriod[];
  observations: Observation[];
} {
  const observations: Observation[] = [];
  const periods: DemoPeriod[] = [];
  const counts = [188, 216, 203, 224, 207];
  let observationId = 1;

  for (let periodIndex = 0; periodIndex < counts.length; periodIndex += 1) {
    const count = counts[periodIndex];
    const start = DEMO_ANCHOR - (counts.length - periodIndex) * 7 * DAY;
    const reset = start + 7 * DAY;
    const capacityBase = [2820, 3010, 2740, 3090, 2890][periodIndex]!;
    const periodObservationIds: number[] = [];
    const trajectory: ParticleTrajectoryPoint[] = [];
    let previousCapacity = capacityBase + signedNoise(7000 + periodIndex) * 220;

    for (let index = 0; index < count; index += 1) {
      const progress = index / Math.max(1, count - 1);
      const wave = Math.sin(progress * Math.PI * 2.4 + periodIndex * 0.7);
      const observedAt =
        start +
        progress * 6.72 * DAY +
        (hash(observationId) - 0.5) * 8 * 60_000;
      const estimatedPercent = Math.min(
        96,
        Math.max(0, 2 + progress * (90 + periodIndex) + wave * 2.2),
      );
      const displayedPercent = Math.round(estimatedPercent);
      const selectedCost = rounded(
        capacityBase * (estimatedPercent / 100) + Math.max(0, wave * 9),
        6,
      );
      const previousCost =
        index === 0
          ? 0
          : observations[observations.length - 1].selected_total_cost;
      const previousPercent =
        index === 0
          ? 0
          : observations[observations.length - 1].estimated_used_percent;
      const deltaCost = rounded(Math.max(0, selectedCost - previousCost), 6);
      const deltaPercent = rounded(
        Math.max(0, estimatedPercent - previousPercent),
        4,
      );
      const volatilitySeed = 12000 + periodIndex * 1000 + index;
      const targetCapacity =
        capacityBase +
        Math.sin(progress * Math.PI * 3.2 + periodIndex * 0.8) * 95 +
        Math.sin(progress * Math.PI * 0.85 + periodIndex) * 70;
      const regularInnovation =
        (signedNoise(volatilitySeed) * 0.7 +
          signedNoise(volatilitySeed + 41) * 0.3) *
        (34 + 42 * (1 - progress));
      const shockRoll = hash(volatilitySeed + 97);
      const shockInnovation =
        index > 2 && shockRoll < 0.125
          ? (signedNoise(volatilitySeed + 131) >= 0 ? 1 : -1) *
            (145 + hash(volatilitySeed + 157) * 360)
          : 0;
      const capacity = rounded(
        Math.min(
          3900,
          Math.max(
            1550,
            previousCapacity +
              (targetCapacity - previousCapacity) * 0.08 +
              regularInnovation +
              shockInnovation,
          ),
        ),
        2,
      );
      previousCapacity = capacity;
      const lowerWidth = 330 + hash(volatilitySeed + 181) * 730;
      const upperWidth = 320 + hash(volatilitySeed + 193) * 680;
      const lower = rounded(Math.max(900, capacity - lowerWidth), 2);
      const upper = rounded(Math.min(4700, capacity + upperWidth), 2);
      const source =
        index === 0
          ? "reset"
          : index % 73 === 0
            ? "exhausted"
            : index % 19 === 0
              ? "manual"
              : "scheduled";
      const itemDiagnostics = diagnostics(
        5200 + observationId,
        estimatedPercent,
        capacity,
        lower,
        upper,
      );
      const essFraction = rounded(0.11 + hash(volatilitySeed + 211) * 0.86, 4);
      const resampled = essFraction < 0.21 || shockInnovation !== 0;
      itemDiagnostics.ess_fraction = essFraction;
      itemDiagnostics.resampled = resampled;
      itemDiagnostics.boundary_mass = {
        lower: rounded(0.008 + hash(volatilitySeed + 223) * 0.12, 4),
        upper: rounded(0.008 + hash(volatilitySeed + 227) * 0.12, 4),
      };
      const itemSnapshots = participantSnapshots(
        participants,
        selectedCost,
        estimatedPercent,
      );
      const excluded = observationId % 389 === 0;
      const fastCorrectionRemainder = observationId % 37;
      const fastCorrectionCalculated =
        fastCorrectionRemainder > 5 && fastCorrectionRemainder < 31;
      const item: Observation = {
        id: observationId,
        observed_at: iso(observedAt),
        source,
        account_id: 8801,
        attribution_started_at: iso(start),
        upstream_resets_at: iso(reset),
        upstream_used_percent: displayedPercent,
        interval_used_percent: rounded(estimatedPercent, 4),
        raw_selected_total_cost: selectedCost,
        selected_total_cost: selectedCost,
        cost_window_started_at: iso(start),
        cost_window_ended_at: iso(observedAt),
        interval_cost_started_at:
          index === 0 ? iso(start) : iso(observedAt - 10 * 60_000),
        interval_cost: deltaCost,
        interval_cost_source: "verified_window",
        normalized_total_cost: selectedCost,
        delta_percent: index === 0 ? null : deltaPercent,
        delta_cost: index === 0 ? null : deltaCost,
        sample_usd_per_percent:
          deltaPercent > 0 ? rounded(deltaCost / deltaPercent, 4) : null,
        effective_usd_per_percent: rounded(capacity / 100, 4),
        estimated_used_percent: rounded(estimatedPercent, 4),
        capacity_lower_usd: lower,
        capacity_upper_usd: upper,
        model_diagnostics: itemDiagnostics,
        fast_correction_usd: fastCorrectionCalculated
          ? rounded(selectedCost * 0.036, 6)
          : null,
        fast_correction_calculated: fastCorrectionCalculated,
        valid_sample: !excluded,
        sample_note: excluded
          ? "演示：管理员排除异常观测"
          : source === "reset"
            ? "检测到新的上游周期"
            : "已按完整成本窗口更新模型",
        rate_method: "time_varying_particle_filter",
        query_mode: index % 17 === 0 ? "direct" : "passive",
        snapshot_sampled_at: iso(observedAt - 25_000),
        participants: itemSnapshots,
        excluded,
        excluded_at: excluded ? iso(observedAt + 5 * 60_000) : null,
        exclusion_reason: excluded ? "演示异常点" : "",
        exclusion_source: excluded ? "manual" : "",
        is_manual_start: index === 0,
        manual_start_reason: index === 0 ? "演示周期起点" : "",
        manual_start_set_at: index === 0 ? iso(observedAt) : null,
        manual_start_end_id: index === 0 ? observationId : null,
        manual_start_end_observed_at: index === 0 ? iso(observedAt) : null,
      };
      observations.push(item);
      periodObservationIds.push(observationId);

      const particles = Array.from({ length: 64 }, (_, particleIndex) => {
        const particleQuantile = (particleIndex - 31.5) / 31.5;
        const sideWidth = particleQuantile < 0 ? lowerWidth : upperWidth;
        return rounded(
          capacity +
            particleQuantile * sideWidth * 0.88 +
            signedNoise(volatilitySeed + particleIndex * 37 + 251) *
              (22 + Math.abs(particleQuantile) * 34),
          2,
        );
      }).sort((left, right) => left - right);
      trajectory.push({
        observation_id: observationId,
        observed_at: item.observed_at,
        source,
        displayed_percent: displayedPercent,
        estimated_percent: item.estimated_used_percent,
        estimated_percent_lower: Math.max(
          0,
          item.estimated_used_percent - 1.25,
        ),
        estimated_percent_upper: Math.min(
          100,
          item.estimated_used_percent + 1.4,
        ),
        capacity_usd: capacity,
        capacity_lower_usd: lower,
        capacity_upper_usd: upper,
        range_min_usd: 1400,
        range_max_usd: 4700,
        range_stage: periodIndex === 2 && index > count * 0.6 ? 1 : 0,
        range_direction:
          periodIndex === 2 && index > count * 0.6 ? "upper" : null,
        ess_fraction: itemDiagnostics.ess_fraction,
        resampled: itemDiagnostics.resampled,
        boundary_mass: itemDiagnostics.boundary_mass,
        particles_usd: particles,
      });
      observationId += 1;
    }

    const promotionPoint = trajectory[Math.floor(trajectory.length * 0.62)];
    periods.push({
      id: periodIndex + 1,
      sequence: periodIndex + 1,
      startedAt: iso(start),
      resetsAt: iso(reset),
      endedAt: iso(reset),
      observationIds: periodObservationIds,
      trajectory,
      promotions:
        periodIndex === 2 && promotionPoint
          ? [
              {
                stage: 1,
                direction: "upper",
                occurred_at: promotionPoint.observed_at,
                from_range_usd: [2100, 3350],
                to_range_usd: [2200, 3700],
                boundary_mass: 0.087,
                display_residual_pp: 0.46,
              },
            ]
          : [],
    });
  }

  return { periods, observations };
}

export { buildPeriods };
