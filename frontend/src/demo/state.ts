import type { PagePermission } from "@/config/pagePermissions";
import type {
  APIUsageBreakdown,
  AppSettingsData,
  BlockedIPAddress,
  CostBreakdown,
  DashboardData,
  HistoricalRebuildPlan,
  LoginEventRecord,
  ModelDiagnostics,
  NotificationRecord,
  Observation,
  MonitoredAccount,
  Participant,
  ParticleTrajectoryData,
  ParticleTrajectoryPeriod,
  ParticleTrajectoryPoint,
  Snapshot,
  Sub2APIUserOption,
  SystemUser,
  UsagePoint,
} from "@/types";

export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "true";

const DEMO_STATE_KEY = "sub2pool:demo:v7:state";
const DEMO_AUTH_KEY = "sub2pool:demo:v2:auth";
const DEMO_ANCHOR = Date.UTC(2026, 7, 12, 4, 0, 0);
const HOUR = 3_600_000;
const DAY = HOUR * 24;

export interface DemoAuthIdentity {
  username: string;
  is_staff: boolean;
  page_permissions: PagePermission[];
  timezone: string;
}

interface DemoPeriod {
  id: number;
  sequence: number;
  startedAt: string;
  resetsAt: string;
  endedAt: string;
  observationIds: number[];
  trajectory: ParticleTrajectoryPoint[];
  promotions: ParticleTrajectoryData["promotions"];
}

export interface DemoState {
  version: 8;
  clock: string;
  nextParticipantId: number;
  nextSystemUserId: number;
  nextObservationId: number;
  nextBlockedId: number;
  revision: number;
  participants: Participant[];
  monitoredAccounts: MonitoredAccount[];
  sub2apiUsers: Sub2APIUserOption[];
  systemUsers: SystemUser[];
  observations: Observation[];
  periods: DemoPeriod[];
  notifications: NotificationRecord[];
  loginEvents: LoginEventRecord[];
  blockedAddresses: BlockedIPAddress[];
  announcementReads: string[];
  settings: AppSettingsData;
  plans: HistoricalRebuildPlan[];
}

function iso(timestamp: number): string {
  return new Date(timestamp).toISOString();
}

function rounded(value: number, digits = 3): number {
  return Number(value.toFixed(digits));
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

function costBreakdown(total: number): CostBreakdown {
  const correction = rounded(total * 0.036, 6);
  return {
    sub2api_cost_usd: rounded(total - correction, 6),
    fast_correction_usd: correction,
    total_cost_usd: rounded(total, 6),
  };
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

function snapshot(
  participant: Participant,
  sharePercent: number,
  cycleCost: number,
  totalPercent: number,
  index: number,
  balance?: number,
): Snapshot {
  const ratios = [0.39, 0.35, 0.26];
  const charged = rounded(totalPercent * ratios[index], 4);
  const selected = rounded(cycleCost * ratios[index], 6);
  const recommended = rounded(
    Math.max(18, sharePercent * 4.4 - selected * 0.22),
    2,
  );
  const current = balance ?? rounded(recommended + [38, -8, 27][index], 2);
  const needsUpdate = Math.abs(current - recommended) >= 20;
  return {
    participant_id: participant.id,
    participant_name: participant.name,
    selected_cost: selected,
    delta_cost: rounded(Math.max(0, selected * 0.017), 6),
    charged_delta_percent: rounded(charged * 0.018, 4),
    charged_cycle_percent: charged,
    charged_percent_lower: rounded(Math.max(0, charged - 0.7), 4),
    charged_percent_upper: rounded(charged + 0.8, 4),
    remaining_share_percent: rounded(Math.max(0, sharePercent - charged), 4),
    current_balance_usd: current,
    recommended_balance_usd: recommended,
    recommended_balance_min_usd: rounded(recommended * 0.9, 2),
    recommended_balance_max_usd: rounded(recommended * 1.1, 2),
    deterministic_balance_min_usd: rounded(recommended * 0.84, 2),
    deterministic_balance_max_usd: rounded(recommended * 1.16, 2),
    balance_difference_usd: rounded(recommended - current, 2),
    is_overused: charged > sharePercent,
    overused_percent: rounded(Math.max(0, charged - sharePercent), 4),
    overused_percent_min: rounded(Math.max(0, charged - sharePercent - 0.5), 4),
    overused_percent_max: rounded(Math.max(0, charged - sharePercent + 0.5), 4),
    needs_manual_update: needsUpdate,
    recommendation_applied: false,
    reason: needsUpdate
      ? "当前余额与模型建议差异超过演示阈值"
      : "当前余额处于模型建议范围",
    allocation_model: "time_varying",
  };
}

function baseMonitoredAccounts(): MonitoredAccount[] {
  return [
    {
      id: 1,
      external_account_id: 8801,
      name: "主力账号",
      enabled: true,
      quota_query_mode: "passive",
      last_local_check_at: iso(DEMO_ANCHOR - 2 * 60_000),
      last_upstream_check_at: iso(DEMO_ANCHOR - 3 * 60_000),
      last_success_at: iso(DEMO_ANCHOR - 2 * 60_000),
      next_local_check_at: iso(DEMO_ANCHOR + 8 * 60_000),
      last_error: "",
    },
    {
      id: 2,
      external_account_id: 8802,
      name: "备用账号",
      enabled: true,
      quota_query_mode: "direct",
      last_local_check_at: iso(DEMO_ANCHOR - 5 * 60_000),
      last_upstream_check_at: iso(DEMO_ANCHOR - 7 * 60_000),
      last_success_at: iso(DEMO_ANCHOR - 5 * 60_000),
      next_local_check_at: iso(DEMO_ANCHOR + 8 * 60_000),
      last_error: "",
    },
  ];
}

function baseParticipants(accounts: MonitoredAccount[]): Participant[] {
  const rows = [
    {
      id: 1,
      name: "青岚",
      email: "owner@example.test",
      sub2api_user_id: 101,
      sub2api_username: "demo-owner",
      sub2api_email: "owner@example.test",
      share_percent: 40,
      is_owner: true,
      notes: "演示车主，由 admin 管理员账号直接查看。",
    },
    {
      id: 2,
      name: "远星",
      email: "starlight@example.test",
      sub2api_user_id: 102,
      sub2api_username: "demo-starlight",
      sub2api_email: "starlight@example.test",
      share_percent: 35,
      is_owner: false,
      notes: "演示参与者，对应系统用户 starlight。",
    },
    {
      id: 3,
      name: "林舟",
      email: "forest@example.test",
      sub2api_user_id: 103,
      sub2api_username: "demo-forest",
      sub2api_email: "forest@example.test",
      share_percent: 25,
      is_owner: false,
      notes: "演示参与者，对应系统用户 forest。",
    },
  ];
  return rows.map((row) => ({
    id: row.id,
    name: row.name,
    email: row.email,
    sub2api_user_id: row.sub2api_user_id,
    sub2api_username: row.sub2api_username,
    sub2api_email: row.sub2api_email,
    sub2api_identity: row.sub2api_username,
    share_percent: row.share_percent,
    is_owner: row.is_owner,
    enabled: true,
    notes: row.notes,
    latest_balance_usd: null,
    last_checked_at: null,
    account_breakdowns: accounts.map((account) => ({
      id: row.id * 10 + account.id,
      account_id: account.id,
      external_account_id: account.external_account_id,
      account_name: account.name,
      account_enabled: account.enabled,
      latest_selected_cost: null,
      last_checked_at: null,
      snapshot: null,
    })),
    snapshot: null,
  }));
}

export function aggregateParticipant(participant: Participant): void {
  const breakdowns = participant.account_breakdowns.filter(
    (item) => item.account_enabled,
  );
  const complete =
    breakdowns.length > 0 && breakdowns.every((item) => item.snapshot !== null);
  const sources = breakdowns.map((breakdown) => {
    const sourceSnapshot = breakdown.snapshot;
    const charged = sourceSnapshot?.charged_cycle_percent ?? 0;
    const chargedLower = sourceSnapshot?.charged_percent_lower ?? charged;
    const chargedUpper = sourceSnapshot?.charged_percent_upper ?? charged;
    const selected = sourceSnapshot?.selected_cost ?? 0;
    const capacity = charged > 0 ? (selected * 100) / charged : 440;
    return {
      account_id: breakdown.account_id,
      external_account_id: breakdown.external_account_id,
      account_name: breakdown.account_name,
      contract_share_percent: participant.share_percent,
      snapshot: sourceSnapshot,
      net_position_usd: sourceSnapshot
        ? ((participant.share_percent - charged) * capacity) / 100
        : null,
      net_position_min_usd: sourceSnapshot
        ? ((participant.share_percent - chargedUpper) * capacity) / 100
        : null,
      net_position_max_usd: sourceSnapshot
        ? ((participant.share_percent - chargedLower) * capacity) / 100
        : null,
      contribution_usd: null as number | null,
      contribution_min_usd: null as number | null,
      contribution_max_usd: null as number | null,
      capacity,
    };
  });
  const pooled = (
    key: "net_position_usd" | "net_position_min_usd" | "net_position_max_usd",
  ) =>
    Math.max(
      0,
      sources.reduce((total, item) => total + (item[key] ?? 0), 0),
    ) * 0.9;
  const recommended = rounded(pooled("net_position_usd"), 2);
  const minimum = rounded(pooled("net_position_min_usd"), 2);
  const maximum = rounded(pooled("net_position_max_usd"), 2);
  const allocate = (
    netKey:
      | "net_position_usd"
      | "net_position_min_usd"
      | "net_position_max_usd",
    outputKey:
      | "contribution_usd"
      | "contribution_min_usd"
      | "contribution_max_usd",
    total: number,
  ) => {
    const positiveTotal = sources.reduce(
      (sum, item) => sum + Math.max(0, item[netKey] ?? 0),
      0,
    );
    for (const source of sources) {
      source[outputKey] =
        complete && positiveTotal > 0
          ? rounded(
              (total * Math.max(0, source[netKey] ?? 0)) / positiveTotal,
              2,
            )
          : complete
            ? 0
            : null;
    }
  };
  allocate("net_position_usd", "contribution_usd", recommended);
  allocate("net_position_min_usd", "contribution_min_usd", minimum);
  allocate("net_position_max_usd", "contribution_max_usd", maximum);
  const selectedCost = rounded(
    breakdowns.reduce(
      (total, item) => total + (item.snapshot?.selected_cost ?? 0),
      0,
    ),
    6,
  );
  const totalCapacity = sources.reduce(
    (total, item) => total + (item.snapshot ? item.capacity : 0),
    0,
  );
  const charged =
    totalCapacity > 0
      ? rounded(
          sources.reduce(
            (total, item) =>
              total +
              (item.snapshot?.charged_cycle_percent ?? 0) * item.capacity,
            0,
          ) / totalCapacity,
          4,
        )
      : 0;
  const balance = participant.latest_balance_usd;
  const difference =
    complete && balance != null
      ? balance < minimum
        ? rounded(minimum - balance, 2)
        : balance > maximum
          ? rounded(maximum - balance, 2)
          : 0
      : null;
  const needsUpdate =
    complete && difference != null && Math.abs(difference) >= 15;
  participant.snapshot = {
    participant_id: participant.id,
    participant_name: participant.name,
    share_percent: participant.share_percent,
    selected_cost: selectedCost,
    charged_cycle_percent: charged,
    current_balance_usd: balance,
    recommended_balance_usd: complete ? recommended : null,
    recommended_balance_min_usd: complete ? minimum : null,
    recommended_balance_max_usd: complete ? maximum : null,
    balance_difference_usd: difference,
    is_overused:
      complete &&
      sources.reduce(
        (total, item) => total + (item.net_position_max_usd ?? 0),
        0,
      ) < 0,
    needs_manual_update: needsUpdate,
    recommendation_applied: false,
    recommendation_complete: complete,
    account_count: breakdowns.length,
    reason: needsUpdate
      ? "全局余额与混池剩余权益区间差异较大"
      : "全局余额处于混池建议区间",
    allocation_model: "pooled_account_sum",
    sources: sources.map(({ capacity: _capacity, ...source }) => source),
  };
}

function participantSnapshots(
  participants: Participant[],
  cycleCost: number,
  usedPercent: number,
): Snapshot[] {
  return participants.map((participant, index) =>
    snapshot(
      participant,
      participant.share_percent,
      cycleCost,
      usedPercent,
      index,
    ),
  );
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
        fast_correction_usd: rounded(selectedCost * 0.036, 6),
        fast_correction_calculated: true,
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

function buildNotifications(participants: Participant[]): NotificationRecord[] {
  const definitions = [
    ["recommendation_changed", "额度建议发生变化", "warning", 2],
    ["limit_exhausted", "参与者余额接近耗尽", "error", 3],
    ["rate_changed", "模型美元 / 1% 发生变化", "info", 1],
    ["collection_error", "一次采集未获得完整快照", "warning", null],
    ["recommendation_changed", "建议余额已重新计算", "info", 3],
    ["limit_exhausted", "余额保护提醒", "warning", 2],
    ["rate_changed", "周期容量区间已收敛", "info", 1],
    ["test", "邮件配置测试", "info", null],
    ["collection_error", "演示采集异常已恢复", "info", null],
    ["recommendation_changed", "新的额度调整建议", "warning", 2],
  ] as const;
  return definitions.map(([type, subject, severity, participantId], index) => {
    const participant = participants.find((item) => item.id === participantId);
    const failed = index === 3;
    const createdAt = DEMO_ANCHOR - index * 11 * HOUR;
    return {
      id: index + 1,
      event_type: type,
      event_type_label:
        (
          {
            recommendation_changed: "建议变化",
            limit_exhausted: "余额耗尽",
            rate_changed: "折算率变化",
            collection_error: "采集异常",
            test: "测试邮件",
          } as Record<string, string>
        )[type] ?? type,
      severity,
      participant_name: participant?.name ?? null,
      recipient: participant?.email ?? "notice@example.test",
      subject: `[演示] ${subject}`,
      body: `这是公开演示生成的通知记录。${participant ? `关联参与者：${participant.name}。` : "系统级事件。"}`,
      status: failed ? "failed" : "sent",
      status_label: failed ? "发送失败" : "已发送",
      error: failed ? "演示 SMTP 暂时不可用" : "",
      created_at: iso(createdAt),
      sent_at: failed ? null : iso(createdAt + 18_000),
    };
  });
}

function buildLoginEvents(): LoginEventRecord[] {
  const addresses = [
    "192.0.2.14",
    "198.51.100.27",
    "203.0.113.8",
    "192.0.2.61",
    "198.51.100.93",
  ];
  return Array.from({ length: 10 }, (_, index) => {
    const success = ![2, 6, 9].includes(index);
    return {
      id: index + 1,
      username:
        index % 3 === 0 ? "admin" : index % 3 === 1 ? "starlight" : "forest",
      success,
      request_ip: addresses[index % addresses.length],
      remote_ip: addresses[(index + 2) % addresses.length],
      webrtc_supported: index % 2 === 0,
      webrtc_ips: index % 2 === 0 ? [`2001:db8::${index + 10}`] : [],
      user_agent: "Sub2Pool Demo Browser",
      failure_reason: success ? "" : "演示：密码错误",
      created_at: iso(DEMO_ANCHOR - index * 7 * HOUR),
    };
  });
}

function baseSettings(): AppSettingsData {
  return {
    monitoring_enabled: true,
    sub2api_base_url: "https://demo.example.test",
    request_timeout_seconds: 20,
    verify_tls: true,
    timezone: "Asia/Shanghai",
    cost_basis: "actual",
    weekly_quota_model: "time_varying",
    fast_correction_enabled: true,
    fast_correction_rules: [
      {
        model_pattern: "*",
        source_multiplier: "2",
        target_multiplier: "2.5",
      },
    ],
    fast_correction_rebuild_recommended: false,
    fast_correction_missing_intervals: 0,
    initial_usd_per_percent: 30,
    safety_factor: 0.92,
    daily_estimate_min_percent_span: 3,
    local_poll_minutes: 10,
    progress_threshold_percent: 1,
    active_max_calibration_hours: 18,
    reset_proximity_minutes: 30,
    stale_warning_hours: 2,
    limit_warning_usd: 20,
    recommendation_change_usd: 15,
    rate_change_alert_percent: 8,
    notify_on_limit_exhausted: true,
    notify_on_recommendation_change: true,
    email_provider: "smtp",
    notify_on_rate_change: true,
    notify_on_collection_error: true,
    notification_cooldown_minutes: 60,
    smtp_host: "smtp.example.test",
    smtp_port: 587,
    smtp_username: "demo@example.test",
    smtp_use_tls: true,
    smtp_use_ssl: false,
    smtp_from_email: "demo@example.test",
    notification_email: "notice@example.test",
    resend_from_email: "Sub2Pool Demo <notice@example.test>",
    resend_api_key_configured: false,
    sub2api_token_configured: true,
    smtp_password_configured: true,
    readonly_api_key_configured: false,
    readonly_api_key_hint: "",
    readonly_api_key_created_at: null,
    last_local_check_at: iso(DEMO_ANCHOR - 2 * 60_000),
    last_upstream_check_at: iso(DEMO_ANCHOR - 3 * 60_000),
    last_success_at: iso(DEMO_ANCHOR - 2 * 60_000),
    last_error: "",
  };
}

function initializeState(): DemoState {
  const monitoredAccounts = baseMonitoredAccounts();
  const participants = baseParticipants(monitoredAccounts);
  const { periods, observations } = buildPeriods(participants);
  const latest = observations[observations.length - 1];
  const latestSnapshots = latest.participants;
  for (const [index, participant] of participants.entries()) {
    const primaryBreakdown = participant.account_breakdowns[0];
    const secondaryBreakdown = participant.account_breakdowns[1];
    const primarySnapshot = latestSnapshots[index] ?? null;
    participant.latest_balance_usd =
      primarySnapshot?.current_balance_usd ?? null;
    participant.last_checked_at = latest.observed_at;
    if (primaryBreakdown) {
      primaryBreakdown.snapshot = primarySnapshot;
      primaryBreakdown.latest_selected_cost =
        primarySnapshot?.selected_cost ?? null;
      primaryBreakdown.last_checked_at = latest.observed_at;
    }
    if (secondaryBreakdown) {
      const secondarySnapshot = snapshot(
        participant,
        participant.share_percent,
        latest.selected_total_cost * 0.62,
        latest.interval_used_percent * 0.72,
        index,
        participant.latest_balance_usd ?? undefined,
      );
      secondaryBreakdown.snapshot = secondarySnapshot;
      secondaryBreakdown.latest_selected_cost = secondarySnapshot.selected_cost;
      secondaryBreakdown.last_checked_at = latest.observed_at;
    }
    aggregateParticipant(participant);
  }
  return {
    version: 8,
    clock: iso(DEMO_ANCHOR),
    nextParticipantId: 4,
    nextSystemUserId: 3,
    nextObservationId: observations.length + 1,
    nextBlockedId: 1,
    revision: 24,
    participants,
    monitoredAccounts,
    sub2apiUsers: participants.map((participant) => ({
      id: participant.sub2api_user_id,
      email: participant.sub2api_email,
      username: participant.sub2api_username,
      status: "active",
      role: participant.is_owner ? "admin" : "user",
    })),
    systemUsers: [
      {
        id: 1,
        username: "starlight",
        email: "starlight@example.test",
        is_active: true,
        page_permissions: ["participants", "particle_filter", "statistics"],
        participant_ids: [2],
        participant_names: ["远星"],
        last_login: iso(DEMO_ANCHOR - 8 * HOUR),
        date_joined: iso(DEMO_ANCHOR - 70 * DAY),
      },
      {
        id: 2,
        username: "forest",
        email: "forest@example.test",
        is_active: true,
        page_permissions: ["participants", "particle_filter", "statistics"],
        participant_ids: [3],
        participant_names: ["林舟"],
        last_login: iso(DEMO_ANCHOR - 19 * HOUR),
        date_joined: iso(DEMO_ANCHOR - 54 * DAY),
      },
    ],
    observations,
    periods,
    notifications: buildNotifications(participants),
    loginEvents: buildLoginEvents(),
    blockedAddresses: [],
    announcementReads: [],
    settings: baseSettings(),
    plans: [],
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function loadDemoState(): DemoState {
  const stored = sessionStorage.getItem(DEMO_STATE_KEY);
  if (stored) {
    try {
      const parsed = JSON.parse(stored) as DemoState;
      if (parsed.version === 8) return parsed;
    } catch {
      sessionStorage.removeItem(DEMO_STATE_KEY);
    }
  }
  const initial = initializeState();
  saveDemoState(initial);
  return initial;
}

export function saveDemoState(state: DemoState): void {
  sessionStorage.setItem(DEMO_STATE_KEY, JSON.stringify(state));
}

export function resetDemoState(): DemoState {
  sessionStorage.removeItem(DEMO_STATE_KEY);
  const state = initializeState();
  saveDemoState(state);
  window.dispatchEvent(new CustomEvent("sub2pool:demo-reset"));
  return state;
}

export function demoIdentity(): DemoAuthIdentity | null {
  const stored = sessionStorage.getItem(DEMO_AUTH_KEY);
  if (!stored) return null;
  try {
    const identity = JSON.parse(stored) as DemoAuthIdentity;
    return identity.username ? identity : null;
  } catch {
    return null;
  }
}

export function setDemoIdentity(identity: DemoAuthIdentity): void {
  sessionStorage.setItem(DEMO_AUTH_KEY, JSON.stringify(identity));
}

export function clearDemoIdentity(): void {
  sessionStorage.removeItem(DEMO_AUTH_KEY);
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
