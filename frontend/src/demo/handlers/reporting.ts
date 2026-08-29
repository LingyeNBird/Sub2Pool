import type { AccountStatusData } from "@/types/accounts";
import type { Observation } from "@/types/observations";
import type { ParticleTrajectoryData } from "@/types/particleTrajectory";
import type { NotificationListData } from "@/types/security";
import type { APIUsageBreakdown, StatisticsData } from "@/types/statistics";

import type { DemoRequestContext } from "../backend";
import {
  apiUsageData,
  participantUsagePoints,
  trajectoryData,
  type DemoState,
} from "../state";

const DAY_MS = 86_400_000;

function notificationsData(context: DemoRequestContext): NotificationListData {
  const { state, url, paginate } = context;
  let items = [...state.notifications];
  const from = url.searchParams.get("from");
  const to = url.searchParams.get("to");
  const type = url.searchParams.get("event_type");
  const participant = url.searchParams.get("participant");
  const subject = url.searchParams.get("subject")?.toLowerCase();
  const status = url.searchParams.get("status");
  if (from) items = items.filter((item) => item.created_at >= from);
  if (to) items = items.filter((item) => item.created_at <= to);
  if (type) items = items.filter((item) => item.event_type === type);
  if (participant) {
    if (participant === "system") {
      items = items.filter((item) => item.participant_name === null);
    } else {
      const participantName = state.participants.find(
        (item) => item.id === Number(participant),
      )?.name;
      items = items.filter((item) => item.participant_name === participantName);
    }
  }
  if (subject) {
    items = items.filter((item) =>
      item.subject.toLowerCase().includes(subject),
    );
  }
  if (status) items = items.filter((item) => item.status === status);
  const page = paginate(items);
  return {
    ...page,
    summary: {
      total: items.length,
      sent_count: items.filter((item) => item.status === "sent").length,
      failed_count: items.filter((item) => item.status === "failed").length,
    },
    filter_options: {
      types: [
        { value: "recommendation_changed", label: "建议变化" },
        { value: "limit_exhausted", label: "余额耗尽" },
        { value: "rate_changed", label: "折算率变化" },
        { value: "collection_error", label: "采集异常" },
        { value: "test", label: "测试邮件" },
      ],
      participants: state.participants.map(({ id, name }) => ({ id, name })),
      statuses: [
        { value: "sent", label: "已发送" },
        { value: "failed", label: "发送失败" },
      ],
    },
  };
}

function statisticsData(state: DemoState, url: URL): StatisticsData {
  const capacityPeriod =
    url.searchParams.get("capacity_period") === "month" ? "month" : "day";
  const capacityDays = Math.max(
    1,
    Number(url.searchParams.get("capacity_days") ?? 90),
  );
  const usageDays = Math.max(
    1,
    Number(url.searchParams.get("usage_days") ?? 7),
  );
  const precisionRaw = url.searchParams.get("usage_precision");
  const usagePrecision =
    precisionRaw === "raw" || precisionRaw === "day" ? precisionRaw : "hour";
  const accountId = Number(url.searchParams.get("account_id"));
  const account =
    state.monitoredAccounts.find((item) => item.id === accountId) ??
    state.monitoredAccounts.find((item) => item.enabled) ??
    state.monitoredAccounts[0]!;
  if (account.provider === "cpa") {
    return {
      account: {
        id: account.id,
        provider: account.provider,
        source_account_id: account.source_account_id,
        external_account_id: account.external_account_id,
        name: account.name,
      },
      capacity_period: capacityPeriod,
      capacity_series: [],
      fast_correction_enabled: false,
      capacity_summary: {
        cycle: null,
        today: {
          estimate_usd: null,
          minimum_usd: null,
          maximum_usd: null,
          start_cost_usd: null,
          start_cost_breakdown: null,
          start_percent: null,
          end_cost_usd: null,
          end_cost_breakdown: null,
          end_percent: null,
          cost_delta_usd: null,
          percent_delta: null,
          sample_count: 0,
          observed_from: null,
          observed_to: null,
          min_percent_span: 3,
          sufficient: false,
          reason: "连接后尚无 CPA 观测记录",
        },
      },
      usage_days: usageDays,
      usage_precision: usagePrecision,
      sample_interval_minutes: Number(state.settings.local_poll_minutes),
      participant_series: [],
      cpa_api_key_series: [],
    };
  }
  const now = Date.parse(state.clock);
  const recentObservations = state.observations.filter(
    (item) => Date.parse(item.observed_at) >= now - capacityDays * 86_400_000,
  );
  const byPeriod = new Map<string, Observation[]>();
  for (const item of recentObservations) {
    const instant = new Date(item.observed_at);
    const key =
      capacityPeriod === "month"
        ? `${instant.getUTCFullYear()}-${String(instant.getUTCMonth() + 1).padStart(2, "0")}`
        : item.observed_at.slice(0, 10);
    const rows = byPeriod.get(key) ?? [];
    rows.push(item);
    byPeriod.set(key, rows);
  }
  const capacitySeries = [...byPeriod.entries()].map(([period, rows]) => {
    const first = rows[0];
    const last = rows.at(-1)!;
    const weekly = last.effective_usd_per_percent * 100;
    const costDelta = Math.max(
      0,
      last.selected_total_cost - first.selected_total_cost,
    );
    const percentDelta = Math.max(
      0,
      last.estimated_used_percent - first.estimated_used_percent,
    );
    return {
      period,
      weekly_total_usd: weekly,
      minimum_usd: last.capacity_lower_usd ?? weekly * 0.9,
      maximum_usd: last.capacity_upper_usd ?? weekly * 1.1,
      sample_count: rows.length,
      basis: {
        observed_at: last.observed_at,
        starts_at: first.observed_at,
        start_cost_usd: first.selected_total_cost,
        start_percent: first.estimated_used_percent,
        start_cost_breakdown: {
          sub2api_cost_usd: first.selected_total_cost * 0.964,
          fast_correction_usd: first.selected_total_cost * 0.036,
          total_cost_usd: first.selected_total_cost,
        },
        end_cost_usd: last.selected_total_cost,
        end_cost_breakdown: {
          sub2api_cost_usd: last.selected_total_cost * 0.964,
          fast_correction_usd: last.selected_total_cost * 0.036,
          total_cost_usd: last.selected_total_cost,
        },
        end_percent: last.estimated_used_percent,
        raw_estimate_usd: weekly,
        estimate_usd: weekly,
        effective_usd_per_percent: last.effective_usd_per_percent,
        calculation_model: "endpoint_ratio" as const,
        rate_source: "演示观测端点",
        sample_note: "由同一周期观测事实生成",
      },
      daily_total_usd:
        percentDelta > 0 ? (costDelta / percentDelta) * 100 : null,
      daily_basis:
        percentDelta > 0
          ? {
              observed_from: first.observed_at,
              observed_to: last.observed_at,
              start_cost_usd: first.selected_total_cost,
              start_cost_breakdown: {
                sub2api_cost_usd: first.selected_total_cost * 0.964,
                fast_correction_usd: first.selected_total_cost * 0.036,
                total_cost_usd: first.selected_total_cost,
              },
              start_percent: first.estimated_used_percent,
              end_cost_usd: last.selected_total_cost,
              end_cost_breakdown: {
                sub2api_cost_usd: last.selected_total_cost * 0.964,
                fast_correction_usd: last.selected_total_cost * 0.036,
                total_cost_usd: last.selected_total_cost,
              },
              end_percent: last.estimated_used_percent,
              cost_delta_usd: costDelta,
              percent_delta: percentDelta,
              estimate_usd: (costDelta / percentDelta) * 100,
              minimum_usd: (costDelta / percentDelta) * 92,
              maximum_usd: (costDelta / percentDelta) * 108,
              sample_count: rows.length,
              min_percent_span: 3,
            }
          : null,
    };
  });
  const latest = state.observations.at(-1)!;
  const currentPeriod = state.periods.at(-1)!;
  const currentRows = state.observations.filter((item) =>
    currentPeriod.observationIds.includes(item.id),
  );
  const todayRows = state.observations.filter(
    (item) => item.observed_at.slice(0, 10) === latest.observed_at.slice(0, 10),
  );
  const todayFirst = todayRows[0] ?? latest;
  const todayCost = latest.selected_total_cost - todayFirst.selected_total_cost;
  const todayPercent =
    latest.estimated_used_percent - todayFirst.estimated_used_percent;
  return {
    account: {
      id: account.id,
      provider: account.provider,
      source_account_id: account.source_account_id,
      external_account_id: account.external_account_id,
      name: account.name,
    },
    capacity_period: capacityPeriod,
    capacity_series: capacitySeries,
    fast_correction_enabled:
      account.provider === "sub2api" &&
      Boolean(state.settings.fast_correction_enabled),
    capacity_summary: {
      cycle: {
        estimate_usd: latest.effective_usd_per_percent * 100,
        raw_estimate_usd: latest.effective_usd_per_percent * 100,
        start_cost_usd: currentRows[0]?.selected_total_cost ?? 0,
        start_cost_breakdown: {
          sub2api_cost_usd: 0,
          fast_correction_usd: 0,
          total_cost_usd: 0,
        },
        start_percent: currentRows[0]?.estimated_used_percent ?? 0,
        end_cost_usd: latest.selected_total_cost,
        end_cost_breakdown: {
          sub2api_cost_usd: latest.selected_total_cost * 0.964,
          fast_correction_usd: latest.selected_total_cost * 0.036,
          total_cost_usd: latest.selected_total_cost,
        },
        end_percent: latest.estimated_used_percent,
        cost_usd: latest.selected_total_cost,
        used_percent: latest.estimated_used_percent,
        effective_usd_per_percent: latest.effective_usd_per_percent,
        calculation_model: "endpoint_ratio",
        rate_calculated: true,
        confidence: "高",
        observed_at: latest.observed_at,
        starts_at: currentPeriod.startedAt,
        resets_at: currentPeriod.resetsAt,
      },
      today: {
        estimate_usd:
          todayPercent > 0 ? (todayCost / todayPercent) * 100 : null,
        minimum_usd: todayPercent > 0 ? (todayCost / todayPercent) * 92 : null,
        maximum_usd: todayPercent > 0 ? (todayCost / todayPercent) * 108 : null,
        start_cost_usd: todayFirst.selected_total_cost,
        start_cost_breakdown: {
          sub2api_cost_usd: todayFirst.selected_total_cost * 0.964,
          fast_correction_usd: todayFirst.selected_total_cost * 0.036,
          total_cost_usd: todayFirst.selected_total_cost,
        },
        start_percent: todayFirst.estimated_used_percent,
        end_cost_usd: latest.selected_total_cost,
        end_cost_breakdown: {
          sub2api_cost_usd: latest.selected_total_cost * 0.964,
          fast_correction_usd: latest.selected_total_cost * 0.036,
          total_cost_usd: latest.selected_total_cost,
        },
        end_percent: latest.estimated_used_percent,
        cost_delta_usd: todayCost,
        percent_delta: todayPercent,
        sample_count: todayRows.length,
        observed_from: todayFirst.observed_at,
        observed_to: latest.observed_at,
        min_percent_span: 3,
        sufficient: todayPercent >= 3,
        reason:
          todayPercent >= 3
            ? "今日观测跨度满足估算要求"
            : "今日百分比跨度仍不足",
      },
    },
    usage_days: usageDays,
    usage_precision: usagePrecision,
    sample_interval_minutes: Number(state.settings.local_poll_minutes),
    participant_series:
      account.provider === "sub2api"
        ? state.participants
            .filter((item) => item.enabled)
            .map((participant) => ({
              participant_id: participant.id,
              participant_name: participant.name,
              account_id: account.id,
              external_account_id: account.external_account_id!,
              sub2api_user_id: participant.sub2api_user_id,
              points: participantUsagePoints(
                state,
                participant.id,
                usageDays,
                usagePrecision,
                account.id,
              ),
            }))
        : [],
    cpa_api_key_series: [],
  };
}

function accountStatusData(state: DemoState): AccountStatusData {
  const sampledAt = new Date(state.clock).getTime();
  const fixtures = [
    {
      usedPercent: 72.4,
      requests: 1842,
      tokens: 8_745_120,
      accountCost: 142.68,
      fastCorrection: 12.48,
      concurrency: 3,
    },
    {
      usedPercent: 28.15,
      requests: 694,
      tokens: 3_126_480,
      accountCost: 54.32,
      fastCorrection: 6.32,
      concurrency: 1,
    },
  ];
  return {
    configured: state.monitoredAccounts.length > 0,
    sampled_at: state.clock,
    stats_days: 30,
    connection_error: null,
    accounts: state.monitoredAccounts.map((account, index) => {
      const fixture = fixtures[index % fixtures.length]!;
      const isCPA = account.provider === "cpa";
      const resetAt = new Date(
        sampledAt + (index === 0 ? 52 : 91) * 3_600_000,
      ).toISOString();
      const cycleCapacity =
        (account.capacity_min_usd + account.capacity_max_usd) / 2;
      const cycles = isCPA
        ? []
        : Array.from({ length: 12 }, (_, cycleIndex) => {
            const endedAt =
              new Date(resetAt).getTime() - (11 - cycleIndex) * 7 * DAY_MS;
            const usedPercent =
              cycleIndex === 11
                ? fixture.usedPercent
                : Number(
                    (
                      38 +
                      ((cycleIndex * 17 + index * 11) % 54) +
                      (cycleIndex % 3) * 0.37
                    ).toFixed(2),
                  );
            return {
              sequence: cycleIndex + 1,
              started_at: new Date(endedAt - 7 * DAY_MS).toISOString(),
              ended_at: new Date(endedAt).toISOString(),
              used_percent: usedPercent,
              used_usd: Number(
                ((cycleCapacity * usedPercent) / 100).toFixed(2),
              ),
              is_current: cycleIndex === 11,
            };
          });
      const statsAccountCost = Number((fixture.accountCost * 3.6).toFixed(2));
      return {
        id: account.id,
        provider: account.provider,
        source_account_id: account.source_account_id,
        external_account_id: account.external_account_id,
        name: account.name,
        enabled: account.enabled,
        quota_query_mode: account.quota_query_mode,
        cycles,
        runtime: {
          name: account.name,
          account_type: isCPA ? "pro" : "oauth",
          status: "active",
          schedulable: true,
          current_concurrency: isCPA ? null : fixture.concurrency,
          concurrency_limit: isCPA ? null : 10,
          last_used_at: isCPA
            ? null
            : new Date(sampledAt - (index + 1) * 85_000).toISOString(),
          rate_limited_at: null,
          rate_limit_reset_at: null,
          overload_until: null,
          temp_unschedulable_until: null,
          temp_unschedulable_reason: null,
          error_message: null,
        },
        usage: {
          source: isCPA ? "cpa_direct" : "passive",
          updated_at: new Date(sampledAt - 95_000).toISOString(),
          five_hour: isCPA
            ? null
            : {
                used_percent: index === 0 ? 18.2 : 6.75,
                reset_at: new Date(sampledAt + 2 * 3_600_000).toISOString(),
                remaining_seconds: 7200,
                request_count: null,
                token_count: null,
                account_cost_usd: null,
                standard_cost_usd: null,
                user_cost_usd: null,
              },
          seven_day: {
            used_percent: fixture.usedPercent,
            reset_at: resetAt,
            remaining_seconds: Math.floor(
              (new Date(resetAt).getTime() - sampledAt) / 1000,
            ),
            request_count: isCPA ? 0 : fixture.requests,
            token_count: isCPA ? 0 : fixture.tokens,
            account_cost_usd: isCPA ? 0 : fixture.accountCost,
            standard_cost_usd: isCPA
              ? null
              : Number((fixture.accountCost / 1.12).toFixed(2)),
            user_cost_usd: isCPA
              ? null
              : Number((fixture.accountCost * 1.08).toFixed(2)),
          },
          needs_verify: isCPA ? null : false,
          is_banned: isCPA ? null : false,
          needs_reauth: isCPA ? null : false,
          error_code: null,
          error: null,
        },
        stats: {
          days: 30,
          actual_days_used: isCPA ? 0 : 26,
          account_cost_usd: isCPA ? 0 : statsAccountCost,
          fast_correction_usd: isCPA ? null : fixture.fastCorrection,
          account_cost_with_fast_correction_usd: isCPA
            ? null
            : Number((statsAccountCost + fixture.fastCorrection).toFixed(2)),
          standard_cost_usd: isCPA
            ? null
            : Number((fixture.accountCost * 3.2).toFixed(2)),
          user_cost_usd: isCPA
            ? null
            : Number((fixture.accountCost * 3.9).toFixed(2)),
          request_count: isCPA ? 0 : fixture.requests * 4,
          token_count: isCPA ? 0 : fixture.tokens * 4,
          avg_daily_cost_usd: isCPA
            ? 0
            : Number((statsAccountCost / 26).toFixed(2)),
          avg_daily_request_count: isCPA
            ? 0
            : Number(((fixture.requests * 4) / 26).toFixed(1)),
          avg_daily_token_count: isCPA
            ? 0
            : Math.round((fixture.tokens * 4) / 26),
          avg_duration_ms: isCPA ? null : index === 0 ? 1348 : 1126,
          today: {
            date: state.clock.slice(0, 10),
            account_cost_usd: isCPA
              ? 0
              : Number((fixture.accountCost * 0.12).toFixed(2)),
            user_cost_usd: isCPA
              ? null
              : Number((fixture.accountCost * 0.13).toFixed(2)),
            request_count: isCPA ? 0 : Math.round(fixture.requests * 0.14),
            token_count: isCPA ? 0 : Math.round(fixture.tokens * 0.14),
          },
        },
        warnings: [],
      };
    }),
  };
}

export function handleReporting(context: DemoRequestContext): Response | null {
  const { method, pathname, state, url, ok } = context;
  if (method === "GET" && pathname === "account-status") {
    return ok(accountStatusData(state));
  }
  if (method === "GET" && pathname === "particle-trajectory") {
    const period = url.searchParams.get("period");
    const accountId = url.searchParams.get("account_id");
    return ok(
      trajectoryData(
        state,
        period ? Number(period) : undefined,
        accountId ? Number(accountId) : undefined,
      ) satisfies ParticleTrajectoryData,
    );
  }
  if (method === "GET" && pathname === "statistics") {
    return ok(statisticsData(state, url));
  }
  const apiUsageMatch = /^statistics\/participants\/(\d+)\/api-usage$/.exec(
    pathname,
  );
  if (method === "GET" && apiUsageMatch) {
    const accountId = url.searchParams.get("account_id");
    return ok(
      apiUsageData(
        state,
        Number(apiUsageMatch[1]),
        accountId ? Number(accountId) : undefined,
      ) satisfies APIUsageBreakdown,
    );
  }
  if (method === "GET" && pathname === "notifications") {
    return ok(notificationsData(context));
  }
  return null;
}
