import type { CostBreakdown } from "@/types/common";
import type { DashboardData } from "@/types/dashboard";
import type {
  ParticleTrajectoryData,
  ParticleTrajectoryPeriod,
} from "@/types/particleTrajectory";
import type { APIUsageBreakdown, UsagePoint } from "@/types/statistics";

import type { DemoPeriod, DemoState } from "./state";
import { rounded } from "./participantProjection";
import { DAY } from "./trajectoryFixtures";

function costBreakdown(total: number): CostBreakdown {
  const correction = rounded(total * 0.036, 6);
  return {
    sub2api_cost_usd: rounded(total - correction, 6),
    fast_correction_usd: correction,
    total_cost_usd: rounded(total, 6),
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function periodSummary(period: DemoPeriod): ParticleTrajectoryPeriod {
  return {
    id: period.id,
    sequence: period.sequence,
    started_at: period.startedAt,
    first_observed_at: period.trajectory[0]?.observed_at ?? period.startedAt,
    last_observed_at: period.trajectory.at(-1)?.observed_at ?? period.endedAt,
    resets_at: period.resetsAt,
    ended_at: period.endedAt,
    observation_count: period.observationIds.length,
    is_current: period.id === 5,
  };
}

export function trajectoryData(
  state: DemoState,
  periodId?: number,
  accountId?: number,
): ParticleTrajectoryData {
  const period =
    state.periods.find((item) => item.id === periodId) ?? state.periods.at(-1);
  if (!period || !period.trajectory.length) {
    return { available: false, message: "暂无演示粒子轨迹" };
  }
  const latest = period.trajectory.at(-1)!;
  const account =
    state.monitoredAccounts.find((item) => item.id === accountId) ??
    state.monitoredAccounts.find((item) => item.enabled) ??
    state.monitoredAccounts[0];
  const latestObservation = state.observations.find(
    (item) => item.id === period.observationIds.at(-1),
  );
  return {
    account: account
      ? {
          id: account.id,
          external_account_id: account.external_account_id,
          name: account.name,
        }
      : undefined,
    available: true,
    message: "",
    algorithm: "时变周限粒子滤波（公开演示）",
    seed: 52026 + period.id,
    particle_count: 1600,
    representative_particle_count: latest.particles_usd.length,
    credible_mass_percent: 90,
    selected_period_id: period.id,
    periods: state.periods.map(periodSummary),
    segment: {
      started_at: period.startedAt,
      first_observed_at: period.trajectory[0].observed_at,
      resets_at: period.resetsAt,
      reason: "upstream_reset",
      reason_label: "上游周期重置",
      observation_count: period.observationIds.length,
    },
    cycle_usage: {
      observed_at: latest.observed_at,
      estimated_used_percent: latest.estimated_percent,
      displayed_used_percent: latest.displayed_percent,
      account_total_usd: latestObservation?.selected_total_cost ?? 0,
      participants:
        latestObservation?.participants.map((snapshot) => ({
          participant_id: snapshot.participant_id,
          participant_name: snapshot.participant_name,
          is_owner:
            state.participants.find(
              (participant) => participant.id === snapshot.participant_id,
            )?.is_owner ?? false,
          used_usd: snapshot.selected_cost,
        })) ?? [],
    },
    latest: {
      observed_at: latest.observed_at,
      capacity_usd: latest.capacity_usd,
      capacity_lower_usd: latest.capacity_lower_usd,
      capacity_upper_usd: latest.capacity_upper_usd,
      range_min_usd: latest.range_min_usd,
      range_max_usd: latest.range_max_usd,
      range_stage: latest.range_stage,
      ess_fraction: latest.ess_fraction,
    },
    points: period.trajectory,
    promotions: period.promotions,
  };
}

export function dashboardData(
  state: DemoState,
  accountId?: number,
): DashboardData {
  const latest = state.observations.at(-1)!;
  const account =
    state.monitoredAccounts.find((item) => item.id === accountId) ??
    state.monitoredAccounts.find((item) => item.enabled) ??
    state.monitoredAccounts[0];
  const participantRows = state.participants.filter(
    (participant) =>
      participant.enabled && participant.snapshot?.needs_manual_update,
  );
  return {
    configured: state.monitoredAccounts.length > 0,
    monitoring_enabled: Boolean(state.settings.monitoring_enabled),
    accounts: clone(state.monitoredAccounts),
    selected_account_id: account?.id ?? null,
    last_local_check_at: account?.last_local_check_at ?? null,
    last_upstream_check_at: account?.last_upstream_check_at ?? null,
    snapshot_stale: false,
    last_success_at: account?.last_success_at ?? null,
    last_error: account?.last_error ?? "",
    sub2api_admin_url: "",
    fast_correction_enabled: Boolean(state.settings.fast_correction_enabled),
    quota_query_mode: account?.quota_query_mode ?? null,
    weekly_quota_model: state.settings.weekly_quota_model,
    needs_manual_update_count: participantRows.length,
    cycle: {
      id: latest.id,
      observed_at: latest.observed_at,
      starts_at: latest.attribution_started_at!,
      resets_at: latest.upstream_resets_at,
      upstream_used_percent: latest.upstream_used_percent,
      interval_used_percent: latest.interval_used_percent,
      effective_usd_per_percent: latest.effective_usd_per_percent,
      selected_total_cost: latest.selected_total_cost,
      selected_total_cost_breakdown: costBreakdown(latest.selected_total_cost),
      start_cost_breakdown: costBreakdown(0),
      unattributed_used_percent: rounded(
        Math.max(
          0,
          latest.estimated_used_percent -
            latest.participants.reduce(
              (sum, item) => sum + item.charged_cycle_percent,
              0,
            ),
        ),
        4,
      ),
      sample_note: latest.sample_note,
      snapshot_sampled_at: latest.snapshot_sampled_at,
      rate_calculated: true,
      estimated_used_percent: latest.estimated_used_percent,
      capacity_lower_usd: latest.capacity_lower_usd,
      capacity_upper_usd: latest.capacity_upper_usd,
      model_diagnostics: latest.model_diagnostics,
    },
    participants: clone(participantRows),
  };
}

export function apiUsageData(
  state: DemoState,
  participantId: number,
  accountId?: number,
): APIUsageBreakdown {
  const participant = state.participants.find(
    (item) => item.id === participantId,
  )!;
  const breakdown =
    participant.account_breakdowns.find(
      (item) => item.account_id === accountId,
    ) ?? participant.account_breakdowns.find((item) => item.account_enabled);
  const total = breakdown?.latest_selected_cost ?? 0;
  const latest = state.observations.at(-1)!;
  const period = state.periods.at(-1)!;
  const weights = [0.58, 0.29, 0.13];
  return {
    participant_id: participant.id,
    participant_name: participant.name,
    sub2api_user_id: participant.sub2api_user_id,
    starts_at: period.startedAt,
    observed_to: latest.observed_at,
    cost_basis: "actual",
    fast_correction_enabled: Boolean(state.settings.fast_correction_enabled),
    participant_total_usd: total,
    weekly_total_estimate_usd: latest.effective_usd_per_percent * 100,
    participant_weekly_percent: breakdown?.snapshot?.charged_cycle_percent ?? 0,
    api_keys: weights.map((weight, index) => ({
      api_key_id: participant.id * 10 + index + 1,
      name: ["默认工作区", "自动化任务", "开发测试"][index],
      status: index === 2 ? "inactive" : "active",
      usage_usd: rounded(total * weight, 4),
      participant_usage_percent: rounded(weight * 100, 2),
      weekly_quota_percent: rounded(
        (breakdown?.snapshot?.charged_cycle_percent ?? 0) * weight,
        3,
      ),
    })),
  };
}

export function participantUsagePoints(
  state: DemoState,
  participantId: number,
  days: number,
  precision: "raw" | "hour" | "day",
  accountId?: number,
): UsagePoint[] {
  const start = Date.parse(state.clock) - days * DAY;
  const filtered = state.observations.filter(
    (item) => Date.parse(item.observed_at) >= start,
  );
  const accountScale =
    state.monitoredAccounts.find((item) => item.id === accountId)
      ?.external_account_id === 8802
      ? 0.62
      : 1;
  const stride = precision === "raw" ? 1 : precision === "hour" ? 6 : 144;
  return filtered
    .filter((_, index) => index % stride === 0)
    .map((observation) => {
      const participant = observation.participants.find(
        (item) => item.participant_id === participantId,
      );
      return {
        observed_at: observation.observed_at,
        label: observation.observed_at,
        account_cycle_usage_usd:
          (participant?.selected_cost ?? 0) * accountScale,
        balance_usd: participant?.current_balance_usd ?? null,
      };
    });
}
