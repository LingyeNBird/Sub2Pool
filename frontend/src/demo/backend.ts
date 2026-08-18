import type {
  APIUsageBreakdown,
  AppSettingsData,
  BlockedIPAddress,
  FastCorrectionDetail,
  HistoricalRebuildPlan,
  LoginEventData,
  NotificationListData,
  Observation,
  ObservationListData,
  ObservationRebuildResult,
  MonitoredAccount,
  Participant,
  ParticleTrajectoryData,
  StatisticsData,
  SystemUser,
} from "@/types";

import {
  aggregateParticipant,
  apiUsageData,
  clearDemoIdentity,
  dashboardData,
  demoIdentity,
  loadDemoState,
  participantUsagePoints,
  resetDemoState,
  saveDemoState,
  setDemoIdentity,
  trajectoryData,
  type DemoState,
} from "./state";

const JSON_HEADERS = { "Content-Type": "application/json" };
const DEMO_DELAY_MS = 140;

function envelope(data: unknown = null, status = 200): Response {
  return new Response(JSON.stringify({ ok: true, data }), {
    status,
    headers: JSON_HEADERS,
  });
}

function failure(message: string, status = 400, details?: unknown): Response {
  return new Response(JSON.stringify({ ok: false, message, details }), {
    status,
    headers: JSON_HEADERS,
  });
}

function delay(): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, DEMO_DELAY_MS));
}

async function body(options: RequestInit): Promise<Record<string, unknown>> {
  if (!options.body) return {};
  if (typeof options.body === "string") {
    try {
      const parsed = JSON.parse(options.body) as unknown;
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : {};
    } catch {
      return {};
    }
  }
  return {};
}

function paginate<T>(
  items: T[],
  url: URL,
): { items: T[]; pagination: ObservationListData["pagination"] } {
  const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
  const pageSize = Math.min(
    100,
    Math.max(1, Number(url.searchParams.get("page_size") ?? 20)),
  );
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const safePage = Math.min(page, totalPages);
  return {
    items: items.slice((safePage - 1) * pageSize, safePage * pageSize),
    pagination: {
      page: safePage,
      page_size: pageSize,
      total: items.length,
      total_pages: totalPages,
    },
  };
}

function participantNames(state: DemoState, ids: number[]): string[] {
  return ids.flatMap((id) => {
    const participant = state.participants.find((item) => item.id === id);
    return participant ? [participant.name] : [];
  });
}
function participantBreakdowns(
  state: DemoState,
  existing: Participant["account_breakdowns"] = [],
): Participant["account_breakdowns"] {
  return state.monitoredAccounts.map((account) => {
    const previous = existing.find((item) => item.account_id === account.id);
    return {
      id: previous?.id ?? null,
      account_id: account.id,
      external_account_id: account.external_account_id,
      account_name: account.name,
      account_enabled: account.enabled,
      latest_selected_cost: previous?.latest_selected_cost ?? null,
      last_checked_at: previous?.last_checked_at ?? null,
      snapshot: previous?.snapshot ?? null,
    };
  });
}

function observationsData(state: DemoState, url: URL): ObservationListData {
  const accountId = Number(url.searchParams.get("account_id"));
  const account =
    state.monitoredAccounts.find((item) => item.id === accountId) ??
    state.monitoredAccounts.find((item) => item.enabled) ??
    state.monitoredAccounts[0];
  let items = [...state.observations].reverse().map((item) => ({
    ...item,
    account_id: account?.external_account_id ?? item.account_id,
  }));
  const from = url.searchParams.get("from");
  const to = url.searchParams.get("to");
  const source = url.searchParams.get("source");
  const queryMode = url.searchParams.get("query_mode");
  if (from) items = items.filter((item) => item.observed_at >= from);
  if (to) items = items.filter((item) => item.observed_at <= to);
  if (source) items = items.filter((item) => item.source === source);
  if (queryMode) items = items.filter((item) => item.query_mode === queryMode);
  const page = paginate(items, url);
  return {
    ...page,
    account: account
      ? {
          id: account.id,
          external_account_id: account.external_account_id,
          name: account.name,
        }
      : null,
    fast_correction_enabled: Boolean(state.settings.fast_correction_enabled),
    summary: {
      total: items.length,
      valid_count: items.filter((item) => item.valid_sample).length,
      passive_count: items.filter((item) => item.query_mode === "passive")
        .length,
      excluded_count: items.filter((item) => item.excluded).length,
    },
  };
}

function notificationsData(state: DemoState, url: URL): NotificationListData {
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
    if (participant === "system")
      items = items.filter((item) => item.participant_name === null);
    else {
      const participantName = state.participants.find(
        (item) => item.id === Number(participant),
      )?.name;
      items = items.filter((item) => item.participant_name === participantName);
    }
  }
  if (subject)
    items = items.filter((item) =>
      item.subject.toLowerCase().includes(subject),
    );
  if (status) items = items.filter((item) => item.status === status);
  const page = paginate(items, url);
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

function loginEventsData(state: DemoState, url: URL): LoginEventData {
  const page = paginate(state.loginEvents, url);
  return {
    ...page,
    success_count: state.loginEvents.filter((item) => item.success).length,
    failure_count: state.loginEvents.filter((item) => !item.success).length,
    unique_request_ips: new Set(
      state.loginEvents.flatMap((item) =>
        item.request_ip ? [item.request_ip] : [],
      ),
    ).size,
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
      external_account_id: account.external_account_id,
      name: account.name,
    },
    capacity_period: capacityPeriod,
    capacity_series: capacitySeries,
    fast_correction_enabled: Boolean(state.settings.fast_correction_enabled),
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
    participant_series: state.participants
      .filter((item) => item.enabled)
      .map((participant) => ({
        participant_id: participant.id,
        participant_name: participant.name,
        account_id: account.id,
        external_account_id: account.external_account_id,
        sub2api_user_id: participant.sub2api_user_id,
        points: participantUsagePoints(
          state,
          participant.id,
          usageDays,
          usagePrecision,
          account.id,
        ),
      })),
  };
}

function fastCorrectionData(
  state: DemoState,
  observation: Observation,
): FastCorrectionDetail {
  const totalRequests = Math.max(
    12,
    Math.round((observation.delta_cost ?? 8) * 9),
  );
  const fastRequests = Math.round(totalRequests * 0.34);
  const fastCost = observation.fast_correction_usd ?? 0;
  return {
    observation_id: observation.id,
    started_at: observation.interval_cost_started_at,
    ended_at: observation.observed_at,
    calculated: true,
    cost_basis: "actual",
    cost_basis_label: "实际扣费",
    request_count: totalRequests,
    fast_request_count: fastRequests,
    non_fast_request_count: totalRequests - fastRequests,
    fast_billed_cost_usd: fastCost * 2,
    correction_usd: fastCost,
    corrected_fast_cost_usd: fastCost * 3,
    sub2api_fast_multiplier: 2,
    upstream_fast_multiplier: 3,
    correction_ratio: 0.5,
    collection_error: "",
    users: state.participants.map((participant, index) => {
      const requestCount = Math.round(
        totalRequests * [0.39, 0.35, 0.26][index],
      );
      const participantFast = Math.round(requestCount * 0.34);
      const billed = fastCost * [0.39, 0.35, 0.26][index] * 2;
      return {
        sub2api_user_id: participant.sub2api_user_id,
        username: participant.sub2api_username,
        email: participant.sub2api_email,
        display_name: participant.name,
        request_count: requestCount,
        fast_request_count: participantFast,
        non_fast_request_count: requestCount - participantFast,
        fast_billed_cost_usd: billed,
        correction_usd: billed * 0.5,
        corrected_fast_cost_usd: billed * 1.5,
      };
    }),
  };
}

function createPlan(
  state: DemoState,
  accountId: number,
): HistoricalRebuildPlan {
  const now = Date.now();
  const id = `demo-plan-${state.plans.length + 1}`;
  const account =
    state.monitoredAccounts.find((item) => item.id === accountId) ??
    state.monitoredAccounts[0]!;
  const plan: HistoricalRebuildPlan = {
    id,
    account_id: account.external_account_id,
    state: "ready",
    digest: `demo_digest_${String(state.revision).padStart(4, "0")}_${state.observations.length}`,
    created_at: new Date(now).toISOString(),
    expires_at: new Date(now + 30 * 60 * 1000).toISOString(),
    base_revision: state.revision,
    result_revision: null,
    blockers: [],
    replay_summary: {},
    safe_to_apply: true,
    algorithm_version: "demo-replay-v1",
    build_id: "github-pages-demo",
  };
  state.plans.push(plan);
  return plan;
}

function authorized(): Response | null {
  return demoIdentity() ? null : failure("登录已过期，请重新登录", 401);
}

export async function demoRequest(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  await delay();
  const url = new URL(path.replace(/^\//, ""), "https://demo.invalid/");
  const pathname = url.pathname.replace(/^\//, "").replace(/\/$/, "");
  const method = (options.method ?? "GET").toUpperCase();
  const payload = await body(options);
  let state = loadDemoState();

  if (method === "GET" && pathname === "auth/client-config") {
    return envelope({ webrtc_enabled: false, stun_url: "" });
  }
  if (method === "POST" && pathname === "auth/network-check") {
    return envelope({ allowed: true });
  }
  if (method === "POST" && pathname === "auth/login") {
    if (payload.username !== "admin" || payload.password !== "123456") {
      return failure("用户名或密码错误", 401);
    }
    const identity = {
      username: "admin",
      is_staff: true,
      timezone: "Asia/Shanghai",
    };
    setDemoIdentity(identity);
    return envelope({ ...identity, access: "demo_access_public_pages" });
  }
  if (
    (method === "GET" && pathname === "auth/me") ||
    (method === "POST" && pathname === "auth/refresh")
  ) {
    const identity = demoIdentity();
    return identity
      ? envelope(
          method === "POST" ? { access: "demo_access_public_pages" } : identity,
        )
      : failure("登录已过期，请重新登录", 401);
  }
  if (method === "POST" && pathname === "auth/logout") {
    clearDemoIdentity();
    return envelope();
  }

  const denied = authorized();
  if (denied) return denied;

  if (method === "POST" && pathname === "auth/password") {
    return envelope({ changed: true, access: "demo_access_public_pages" });
  }
  if (method === "GET" && pathname === "dashboard") {
    const accountId = Number(url.searchParams.get("account_id"));
    return envelope(dashboardData(state, accountId));
  }
  if (pathname === "monitor/run" && method === "GET") {
    const enabledAccounts = state.monitoredAccounts.filter(
      (account) => account.enabled,
    );
    const nextChecks = enabledAccounts.flatMap((account) =>
      account.next_local_check_at ? [account.next_local_check_at] : [],
    );
    return envelope({
      monitoring_enabled: Boolean(state.settings.monitoring_enabled),
      interval_seconds: Number(state.settings.local_poll_minutes) * 60,
      next_local_check_at: nextChecks.sort()[0] ?? null,
      run_in_progress: false,
      accounts: state.monitoredAccounts.map((account) => ({
        id: account.id,
        external_account_id: account.external_account_id,
        name: account.name,
        enabled: account.enabled,
        next_local_check_at: account.next_local_check_at,
        run_in_progress: false,
      })),
      server_time: state.clock,
    });
  }
  if (pathname === "monitor/run" && method === "POST") {
    const latest = state.observations.at(-1)!;
    const accountId = Number(payload.account_id);
    const accounts =
      Number.isFinite(accountId) && accountId > 0
        ? state.monitoredAccounts.filter((account) => account.id === accountId)
        : state.monitoredAccounts.filter((account) => account.enabled);
    state.clock = new Date(Date.parse(state.clock) + 10 * 60_000).toISOString();
    for (const account of accounts) {
      account.last_local_check_at = state.clock;
      account.last_upstream_check_at = state.clock;
      account.last_success_at = state.clock;
      account.next_local_check_at = new Date(
        Date.parse(state.clock) + 10 * 60_000,
      ).toISOString();
      account.last_error = "";
    }
    latest.sample_note = "演示：已执行一次本地测算，状态已在当前标签页更新";
    saveDemoState(state);
    return envelope({ scheduled: true });
  }
  const applyRecommendation =
    /^dashboard\/participants\/(\d+)\/apply-recommendation$/.exec(pathname);
  if (method === "POST" && applyRecommendation) {
    const participant = state.participants.find(
      (item) => item.id === Number(applyRecommendation[1]),
    );
    if (!participant?.snapshot?.recommended_balance_usd)
      return failure("该参与者暂无可应用建议", 409);
    participant.snapshot.current_balance_usd =
      participant.snapshot.recommended_balance_usd;
    participant.snapshot.balance_difference_usd = 0;
    participant.snapshot.needs_manual_update = false;
    participant.snapshot.recommendation_applied = true;
    participant.latest_balance_usd = participant.snapshot.current_balance_usd;
    for (const breakdown of participant.account_breakdowns) {
      if (!breakdown.snapshot) continue;
      breakdown.snapshot.current_balance_usd =
        participant.snapshot.current_balance_usd;
    }
    saveDemoState(state);
    return envelope({ applied: true });
  }
  if (method === "GET" && pathname === "participants")
    return envelope(state.participants);
  if (method === "GET" && pathname === "participants/sub2api-users")
    return envelope(state.sub2apiUsers);
  if (method === "POST" && pathname === "participants") {
    const id = state.nextParticipantId++;
    const participant = {
      id,
      name: String(payload.name ?? `演示参与者 ${id}`),
      email: String(payload.email ?? `participant-${id}@example.test`),
      sub2api_user_id: Number(payload.sub2api_user_id ?? 100 + id),
      sub2api_username: String(payload.sub2api_username ?? `demo-user-${id}`),
      sub2api_email: String(
        payload.sub2api_email ?? `participant-${id}@example.test`,
      ),
      sub2api_identity: String(payload.sub2api_username ?? `demo-user-${id}`),
      share_percent: Number(payload.share_percent ?? 0),
      is_owner: Boolean(payload.is_owner),
      enabled: payload.enabled !== false,
      notes: String(payload.notes ?? ""),
      latest_balance_usd: null,
      last_checked_at: null,
      account_breakdowns: participantBreakdowns(state),
      snapshot: null,
    } satisfies Participant;
    aggregateParticipant(participant);
    state.participants.push(participant);
    saveDemoState(state);
    return envelope(participant, 201);
  }
  const participantMatch = /^participants\/(\d+)$/.exec(pathname);
  if (participantMatch && method === "PUT") {
    const participant = state.participants.find(
      (item) => item.id === Number(participantMatch[1]),
    );
    if (!participant) return failure("参与者不存在", 404);
    Object.assign(participant, payload, {
      sub2api_identity: String(
        payload.sub2api_username ?? participant.sub2api_username,
      ),
      share_percent: Number(payload.share_percent ?? participant.share_percent),
      is_owner: Boolean(payload.is_owner ?? participant.is_owner),
      account_breakdowns: participantBreakdowns(
        state,
        participant.account_breakdowns,
      ),
    });
    aggregateParticipant(participant);
    saveDemoState(state);
    return envelope(participant);
  }
  if (participantMatch && method === "DELETE") {
    const id = Number(participantMatch[1]);
    const participant = state.participants.find((item) => item.id === id);
    if (!participant) return failure("参与者不存在", 404);
    participant.enabled = false;
    for (const user of state.systemUsers) {
      user.participant_ids = user.participant_ids.filter(
        (participantId) => participantId !== id,
      );
      user.participant_names = participantNames(state, user.participant_ids);
    }
    saveDemoState(state);
    return envelope({ disabled: true });
  }
  if (method === "GET" && pathname === "system-users")
    return envelope(state.systemUsers);
  if (method === "POST" && pathname === "system-users") {
    const username = String(payload.username ?? "").trim();
    if (state.systemUsers.some((item) => item.username === username)) {
      return failure("用户字段格式无效", 400, { username: ["用户名已存在"] });
    }
    const participantIds = Array.isArray(payload.participant_ids)
      ? payload.participant_ids.map(Number)
      : [];
    const item: SystemUser = {
      id: state.nextSystemUserId++,
      username,
      email: String(payload.email ?? ""),
      is_active: payload.is_active !== false,
      participant_ids: participantIds,
      participant_names: participantNames(state, participantIds),
      last_login: null,
      date_joined: state.clock,
    };
    state.systemUsers.push(item);
    saveDemoState(state);
    return envelope(item, 201);
  }
  const systemUserMatch = /^system-users\/(\d+)$/.exec(pathname);
  if (systemUserMatch && method === "PATCH") {
    const item = state.systemUsers.find(
      (user) => user.id === Number(systemUserMatch[1]),
    );
    if (!item) return failure("系统用户不存在", 404);
    const participantIds = Array.isArray(payload.participant_ids)
      ? payload.participant_ids.map(Number)
      : item.participant_ids;
    Object.assign(item, payload, {
      participant_ids: participantIds,
      participant_names: participantNames(state, participantIds),
    });
    saveDemoState(state);
    return envelope(item);
  }
  if (systemUserMatch && method === "DELETE") {
    state.systemUsers = state.systemUsers.filter(
      (item) => item.id !== Number(systemUserMatch[1]),
    );
    saveDemoState(state);
    return envelope();
  }
  if (method === "GET" && pathname === "observations")
    return envelope(observationsData(state, url));
  const fastMatch = /^observations\/(\d+)\/fast-correction$/.exec(pathname);
  if (method === "GET" && fastMatch) {
    const observation = state.observations.find(
      (item) => item.id === Number(fastMatch[1]),
    );
    return observation
      ? envelope(fastCorrectionData(state, observation))
      : failure("观测不存在", 404);
  }
  const exclusionMatch = /^observations\/(\d+)\/(exclude|restore)$/.exec(
    pathname,
  );
  if (method === "POST" && exclusionMatch) {
    const observation = state.observations.find(
      (item) => item.id === Number(exclusionMatch[1]),
    );
    if (!observation) return failure("观测不存在", 404);
    const excluded = exclusionMatch[2] === "exclude";
    observation.excluded = excluded;
    observation.valid_sample = !excluded;
    observation.excluded_at = excluded ? state.clock : null;
    observation.exclusion_reason = excluded
      ? String(payload.reason ?? "管理员手动排除")
      : "";
    observation.exclusion_source = excluded ? "manual" : "";
    saveDemoState(state);
    return envelope({ replayed: true });
  }
  const manualStartMatch = /^observations\/(\d+)\/manual-start$/.exec(pathname);
  if (manualStartMatch && (method === "POST" || method === "DELETE")) {
    const observation = state.observations.find(
      (item) => item.id === Number(manualStartMatch[1]),
    );
    if (!observation) return failure("观测不存在", 404);
    if (method === "POST") {
      const endObservationId =
        typeof payload.end_observation_id === "number"
          ? payload.end_observation_id
          : observation.id;
      const endObservation = state.observations.find(
        (item) => item.id === endObservationId,
      );
      if (!endObservation) return failure("起点区间终点记录不存在", 404);
      const compare = (left: Observation, right: Observation) =>
        Date.parse(left.observed_at) - Date.parse(right.observed_at) ||
        left.id - right.id;
      if (
        observation.account_id !== endObservation.account_id ||
        compare(endObservation, observation) < 0
      ) {
        return failure("起点区间终点记录无效", 400);
      }
      for (const item of state.observations) {
        if (item.id === observation.id || !item.is_manual_start) continue;
        const existingEnd =
          state.observations.find(
            (candidate) => candidate.id === item.manual_start_end_id,
          ) ?? item;
        const overlaps =
          compare(item, endObservation) <= 0 &&
          compare(observation, existingEnd) <= 0;
        if (!overlaps) continue;
        const contained =
          compare(observation, item) <= 0 &&
          compare(existingEnd, endObservation) <= 0;
        if (!contained) return failure("起点区间与现有区间部分重叠", 400);
        item.is_manual_start = false;
        item.manual_start_reason = "";
        item.manual_start_set_at = null;
        item.manual_start_end_id = null;
        item.manual_start_end_observed_at = null;
      }
      observation.is_manual_start = true;
      observation.manual_start_reason = String(payload.reason ?? "");
      observation.manual_start_set_at = state.clock;
      observation.manual_start_end_id = endObservation.id;
      observation.manual_start_end_observed_at = endObservation.observed_at;
    } else {
      observation.is_manual_start = false;
      observation.manual_start_reason = "";
      observation.manual_start_set_at = null;
      observation.manual_start_end_id = null;
      observation.manual_start_end_observed_at = null;
    }
    saveDemoState(state);
    return envelope({ replayed: true });
  }
  if (method === "POST" && pathname === "observations/rebuild") {
    const result: ObservationRebuildResult = {
      rebuilt_observations: state.observations.length,
      automatic_exclusions: state.observations.filter(
        (item) => item.exclusion_source === "automatic",
      ).length,
      inferred_intervals: 0,
      latest_observation_id: state.observations.at(-1)?.id ?? null,
      replay_started_at: state.periods[0].startedAt,
    };
    return envelope(result);
  }
  if (method === "GET" && pathname === "particle-trajectory") {
    const period = url.searchParams.get("period");
    const accountId = url.searchParams.get("account_id");
    return envelope(
      trajectoryData(
        state,
        period ? Number(period) : undefined,
        accountId ? Number(accountId) : undefined,
      ) satisfies ParticleTrajectoryData,
    );
  }
  if (method === "GET" && pathname === "statistics") {
    return envelope(statisticsData(state, url));
  }
  const apiUsageMatch = /^statistics\/participants\/(\d+)\/api-usage$/.exec(
    pathname,
  );
  if (method === "GET" && apiUsageMatch) {
    const accountId = url.searchParams.get("account_id");
    return envelope(
      apiUsageData(
        state,
        Number(apiUsageMatch[1]),
        accountId ? Number(accountId) : undefined,
      ) satisfies APIUsageBreakdown,
    );
  }
  if (method === "GET" && pathname === "notifications") {
    return envelope(notificationsData(state, url));
  }
  if (method === "GET" && pathname === "login-events") {
    return envelope(loginEventsData(state, url));
  }
  if (method === "GET" && pathname === "ip-blocks") {
    return envelope(state.blockedAddresses);
  }
  if (method === "POST" && pathname === "ip-blocks") {
    if (
      state.blockedAddresses.some((item) => item.address === payload.address)
    ) {
      return failure("该地址已经封禁", 400);
    }
    const item: BlockedIPAddress = {
      id: state.nextBlockedId++,
      address: String(payload.address ?? ""),
      source_type:
        payload.source_type === "remote" || payload.source_type === "webrtc"
          ? payload.source_type
          : "request",
      source_label:
        payload.source_type === "webrtc"
          ? "WebRTC 地址"
          : payload.source_type === "remote"
            ? "直连地址"
            : "服务器来源 IP",
      notes: String(payload.notes ?? ""),
      login_event_id:
        payload.login_event_id == null ? null : Number(payload.login_event_id),
      created_at: state.clock,
    };
    state.blockedAddresses.push(item);
    saveDemoState(state);
    return envelope(item, 201);
  }
  const ipBlockMatch = /^ip-blocks\/(\d+)$/.exec(pathname);
  if (method === "DELETE" && ipBlockMatch) {
    state.blockedAddresses = state.blockedAddresses.filter(
      (item) => item.id !== Number(ipBlockMatch[1]),
    );
    saveDemoState(state);
    return envelope();
  }
  if (method === "GET" && pathname === "settings/monitored-accounts") {
    return envelope(state.monitoredAccounts);
  }
  if (method === "POST" && pathname === "settings/monitored-accounts") {
    const externalAccountId = Number(payload.external_account_id);
    if (
      state.monitoredAccounts.some(
        (account) => account.external_account_id === externalAccountId,
      )
    ) {
      return failure("该上游账号已经在监控列表中", 400);
    }
    const account: MonitoredAccount = {
      id: Math.max(0, ...state.monitoredAccounts.map((item) => item.id)) + 1,
      external_account_id: externalAccountId,
      name: String(payload.name ?? `OpenAI 账号 ${externalAccountId}`),
      enabled: payload.enabled !== false,
      quota_query_mode:
        payload.quota_query_mode === "direct" ? "direct" : "passive",
      last_local_check_at: null,
      last_upstream_check_at: null,
      last_success_at: null,
      next_local_check_at: null,
      last_error: "",
    };
    state.monitoredAccounts.push(account);
    for (const participant of state.participants) {
      participant.account_breakdowns.push({
        id: null,
        account_id: account.id,
        external_account_id: account.external_account_id,
        account_name: account.name,
        account_enabled: account.enabled,
        latest_selected_cost: null,
        last_checked_at: null,
        snapshot: null,
      });
    }
    for (const participant of state.participants) {
      aggregateParticipant(participant);
    }
    saveDemoState(state);
    return envelope(account, 201);
  }
  const monitoredAccountMatch = /^settings\/monitored-accounts\/(\d+)$/.exec(
    pathname,
  );
  if (monitoredAccountMatch && method === "PUT") {
    const account = state.monitoredAccounts.find(
      (item) => item.id === Number(monitoredAccountMatch[1]),
    );
    if (!account) return failure("监控账号不存在", 404);
    account.external_account_id = Number(
      payload.external_account_id ?? account.external_account_id,
    );
    account.name = String(payload.name ?? account.name);
    account.enabled = payload.enabled !== false;
    account.quota_query_mode =
      payload.quota_query_mode === "direct" ? "direct" : "passive";
    for (const participant of state.participants) {
      const breakdown = participant.account_breakdowns.find(
        (item) => item.account_id === account.id,
      );
      if (!breakdown) continue;
      breakdown.external_account_id = account.external_account_id;
      breakdown.account_name = account.name;
      breakdown.account_enabled = account.enabled;
    }
    for (const participant of state.participants) {
      aggregateParticipant(participant);
    }
    saveDemoState(state);
    return envelope(account);
  }
  if (monitoredAccountMatch && method === "DELETE") {
    const accountId = Number(monitoredAccountMatch[1]);
    state.monitoredAccounts = state.monitoredAccounts.filter(
      (item) => item.id !== accountId,
    );
    for (const participant of state.participants) {
      participant.account_breakdowns = participant.account_breakdowns.filter(
        (item) => item.account_id !== accountId,
      );
    }
    for (const participant of state.participants) {
      aggregateParticipant(participant);
    }
    saveDemoState(state);
    return envelope();
  }
  if (method === "GET" && pathname === "settings") {
    return envelope(state.settings satisfies AppSettingsData);
  }
  if (method === "PATCH" && pathname === "settings") {
    const secretKeys: Record<string, true> = {
      sub2api_admin_token: true,
      smtp_password: true,
      resend_api_key: true,
    };
    for (const [key, value] of Object.entries(payload)) {
      if (secretKeys[key]) {
        if (value) {
          state.settings[
            `${key.replace(/_admin_token$|_password$|_api_key$/, "")}_configured`
          ] = true;
        }
      } else {
        state.settings[key] = value as string | number | boolean | null;
      }
    }
    saveDemoState(state);
    return envelope(state.settings);
  }
  if (method === "POST" && pathname === "settings/openai-accounts") {
    return envelope([
      {
        id: 8801,
        name: "演示 OpenAI 主力账号",
        type: "openai",
        status: "active",
        schedulable: true,
      },
      {
        id: 8802,
        name: "演示 OpenAI 备用账号",
        type: "openai",
        status: "active",
        schedulable: true,
      },
      {
        id: 8803,
        name: "演示 OpenAI 待添加账号",
        type: "openai",
        status: "active",
        schedulable: true,
      },
    ]);
  }
  if (
    method === "POST" &&
    (pathname === "settings/test-sub2api" || pathname === "settings/test-email")
  ) {
    return envelope({
      demo: true,
      connected: pathname.endsWith("sub2api"),
      sent: false,
    });
  }
  if (pathname === "settings/readonly-api-key" && method === "POST") {
    const generated = {
      api_key: "demo_public_readonly_key_not_a_secret",
      hint: "demo_...cret",
      created_at: state.clock,
    };
    state.settings.readonly_api_key_configured = true;
    state.settings.readonly_api_key_hint = generated.hint;
    state.settings.readonly_api_key_created_at = generated.created_at;
    saveDemoState(state);
    return envelope(generated);
  }
  if (pathname === "settings/readonly-api-key" && method === "DELETE") {
    state.settings.readonly_api_key_configured = false;
    state.settings.readonly_api_key_hint = "";
    state.settings.readonly_api_key_created_at = null;
    saveDemoState(state);
    return envelope({ revoked: true });
  }
  if (
    method === "POST" &&
    pathname === "settings/data-maintenance/history-rebuild-plans"
  ) {
    const keys = Object.keys(payload);
    if (
      keys.length !== 1 ||
      keys[0] !== "account_id" ||
      !state.monitoredAccounts.some(
        (account) => account.id === Number(payload.account_id),
      )
    ) {
      return failure("必须指定有效的监控账号", 400);
    }
    const plan = createPlan(state, Number(payload.account_id));
    saveDemoState(state);
    return envelope(plan, 201);
  }
  const planMatch =
    /^settings\/data-maintenance\/history-rebuild-plans\/([^/]+)(?:\/(apply))?$/.exec(
      pathname,
    );
  if (planMatch) {
    const plan = state.plans.find((item) => item.id === planMatch[1]);
    if (!plan) return failure("维护计划不存在", 404);
    if (method === "GET" && !planMatch[2]) return envelope(plan);
    if (method === "POST" && planMatch[2] === "apply") {
      const digest = payload.digest;
      if (typeof digest !== "string" || !digest) {
        return failure("apply 必须提交计划 digest", 400);
      }
      if (plan.state === "applied") {
        return digest === plan.digest
          ? envelope(plan)
          : failure("计划已经应用且 digest 不匹配", 409);
      }
      if (plan.state !== "ready" || !plan.safe_to_apply) {
        return failure("计划当前不可应用", 409);
      }
      if (digest !== plan.digest) {
        return failure("计划 digest 不匹配", 409);
      }
      if (Date.parse(plan.expires_at) <= Date.now()) {
        plan.state = "stale";
        plan.safe_to_apply = false;
        saveDemoState(state);
        return failure("计划已过期，请重新创建", 409);
      }
      plan.state = "applied";
      plan.result_revision = ++state.revision;
      plan.safe_to_apply = false;
      plan.replay_summary = {
        rebuilt_observations: state.observations.length,
        automatic_exclusions: 0,
        inferred_intervals: 0,
        latest_observation_id: state.observations.at(-1)?.id ?? null,
      };
      saveDemoState(state);
      return envelope(plan);
    }
  }
  if (method === "GET" && pathname === "database/export") {
    const exportData = {
      warning: "DEMO ONLY - SYNTHETIC DATA - NOT A SQLITE BACKUP",
      generated_at: state.clock,
      participants: state.participants,
      observation_count: state.observations.length,
      period_count: state.periods.length,
    };
    return new Response(JSON.stringify(exportData, null, 2), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  if (method === "POST" && pathname === "database/import") {
    resetDemoState();
    return envelope({ demo_reset: true });
  }

  console.error(`Unhandled demo endpoint: ${method} ${pathname}`);
  return failure(`演示接口尚未覆盖：${method} ${pathname}`, 501);
}
