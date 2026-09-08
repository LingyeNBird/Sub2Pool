import type { CPAQuotaDetail, CPAResetPreview } from "@/types/accounts";
import type { CPAPricingInventory, CPAPricingSync } from "@/types/cpaPricing";
import type {
  CPABillingSummary,
  CPAKeys,
  CPARequest,
  CPAPoolSummary,
  CPAClaim,
} from "@/types/cpa";
import type { DemoRequestContext } from "./backend";
import type { DemoState } from "./state";
import type { CPAAPIKeyUsageSeries } from "@/types/statistics";
import { demoIdentity, saveDemoState } from "./state";

export interface DemoCPAState {
  billingConfigs?: Record<
    number,
    { anchor_date: string | null; timezone: string }
  >;
  quotaStatuses?: Record<number, CPAQuotaDetail>;
  resetPlans?: CPAResetPreview[];
  keys: CPAKeys;
  events: (CPARequest & {
    participant_id: number | null;
    key_id: number;
    account_id: number;
  })[];
  claims: (CPAClaim & { revision: number })[];
}

export function initializeCPADemo(state: DemoState) {
  if (state.cpa) return;
  const account = {
    ...state.monitoredAccounts[0]!,
    id: 3,
    provider: "cpa" as const,
    pool_id: 2,
    external_account_id: null,
    cpa_auth_index: "demo-codex",
    source_account_id: "demo-codex",
    name: "CPA 拼车账号",
    quota_query_mode: "direct" as const,
  };
  state.monitoredAccounts.push(account);
  state.quotaPools.push({
    id: 2,
    name: "CPA 演示池",
    contract_revision: 1,
    account_ids: [3],
    total_share_percent: 100,
    allocations: state.participants.map((p, i) => ({
      participant_id: p.id,
      share_percent: [40, 35, 25][i] ?? 0,
    })),
  });
  state.nextPoolId = Math.max(3, state.nextPoolId);
  for (const user of state.systemUsers) {
    user.account_ids.push(3);
    user.account_names.push(account.name);
  }
  const anchor = Date.parse(state.clock);
  const start = new Date(anchor - 3 * 86400000).toISOString();
  const keys: CPAKeys = {
    keys: state.participants.map((p, i) => ({
      id: i + 1,
      name: ["工作电脑", "笔记本", "开发测试"][i] ?? "演示 Key",
      hint: String(1001 + i),
      observed_hash: String(i + 1).padStart(64, "0"),
      bindings: [
        {
          id: i + 1,
          key_id: i + 1,
          name: ["工作电脑", "笔记本", "开发测试"][i] ?? "演示 Key",
          hint: String(1001 + i),
          participant_id: p.id,
          participant_name: p.name,
          started_at: start,
          ended_at: null,
        },
      ],
    })),
    unregistered: [{ observed_hash: "4".padStart(64, "0"), hint: "1004" }],
  };
  const events: DemoCPAState["events"] = Array.from({ length: 84 }, (_, i) => {
    const owner = i % 4;
    return {
      id: i + 1,
      account_id: 3,
      participant_id: state.participants[owner]?.id ?? null,
      key_id: owner + 1,
      occurred_at: new Date(anchor - (84 - i) * 1800000).toISOString(),
      request_id: `demo-cpa-request-${i + 1}`,
      api_key_hint: String(1001 + owner),
      model: owner === 3 ? "gpt-6-astra" : "gpt-5.4",
      endpoint: "/v1/responses",
      input_tokens: 9000 + i * 70,
      cached_input_tokens: 3000,
      output_tokens: 1100 + i * 13,
      reasoning_tokens: 350,
      total_tokens: 10100 + i * 83,
      failed: i % 17 === 0,
      latency_ms: 1800 + i * 15,
      ttft_ms: 210 + i,
      usage_usd: 0,
      unpriced: false,
      requested_service_tier: "",
      response_service_tier: "default",
    };
  });
  state.cpa = { keys, events, claims: [] };
  repriceCPADemo(state);
}

function demoPrice(state: DemoState, model: string) {
  const pricing = state.settings.cpa_model_pricing;
  const name = model in pricing ? model : model.replace(/-latest$/i, "");
  return pricing[name] ? { name, price: pricing[name] } : null;
}

export function repriceCPADemo(state: DemoState) {
  for (const event of state.cpa?.events ?? []) {
    const match = demoPrice(state, event.model);
    event.unpriced = !match;
    const cached = Math.min(event.input_tokens, event.cached_input_tokens);
    const fast = ["fast", "priority"].includes(
      event.response_service_tier || event.requested_service_tier,
    )
      ? Number(state.settings.cpa_fast_multiplier)
      : 1;
    const context =
      state.settings.cpa_double_billing_enabled &&
      event.input_tokens >
        Number(state.settings.cpa_double_billing_threshold_tokens)
        ? Number(state.settings.cpa_double_billing_multiplier)
        : 1;
    event.usage_usd = match
      ? (((event.input_tokens - cached) * Number(match.price.input) +
          cached * Number(match.price.cached_input) +
          event.output_tokens * Number(match.price.output)) /
          1_000_000) *
        fast *
        context
      : 0;
  }
}

function demoPricingInventory(
  state: DemoState,
  accountId: number | null,
): CPAPricingInventory {
  const groups = new Map<string, CPAPricingInventory["models"][number]>();
  for (const event of state.cpa?.events ?? []) {
    if (accountId != null && event.account_id !== accountId) continue;
    const match = demoPrice(state, event.model);
    const row = groups.get(event.model) ?? {
      model: event.model,
      request_count: 0,
      token_count: 0,
      missing: !match,
      pricing_model: match?.name ?? null,
    };
    row.request_count++;
    row.token_count += event.total_tokens;
    groups.set(event.model, row);
  }
  const models = [...groups.values()];
  return {
    pricing: state.settings.cpa_model_pricing,
    models,
    missing_model_count: models.filter((row) => row.missing).length,
    unpriced_request_count: models
      .filter((row) => row.missing)
      .reduce((sum, row) => sum + row.request_count, 0),
    source: "models.dev（演示快照）",
    source_url: "https://models.dev/api.json",
    generated_at: state.clock,
  };
}

function ownIds(state: DemoState) {
  const identity = demoIdentity();
  if (identity?.is_staff) return null;
  return (
    state.systemUsers.find((user) => user.username === identity?.username)
      ?.participant_ids ?? []
  );
}

function canRead(state: DemoState, accountId: number) {
  const identity = demoIdentity();
  if (identity?.is_staff) return true;
  const user = state.systemUsers.find((u) => u.username === identity?.username);
  const account = state.monitoredAccounts.find((a) => a.id === accountId);
  const pool = state.quotaPools.find((p) => p.id === account?.pool_id);
  return Boolean(
    user?.account_ids.includes(accountId) &&
    pool?.allocations.some((a) =>
      user.participant_ids.includes(a.participant_id),
    ),
  );
}

export function demoCPASummary(
  state: DemoState,
  accountId: number,
): CPAPoolSummary | null {
  const account = state.monitoredAccounts.find(
    (a) => a.id === accountId && a.provider === "cpa",
  );
  const pool = state.quotaPools.find((p) => p.id === account?.pool_id);
  if (!account || !pool || !canRead(state, accountId)) return null;
  const own = ownIds(state);
  const owners = state.participants.filter(
    (p) =>
      p.is_owner &&
      p.enabled &&
      pool.allocations.some((a) => a.participant_id === p.id),
  );
  const owner: CPAPoolSummary["accounts"][number]["owner"] =
    owners.length === 1
      ? {
          participant_id: owners[0]!.id,
          participant_name: owners[0]!.name,
          started_at: state.clock,
          status: "active",
        }
      : {
          participant_id: null,
          participant_name: null,
          started_at: null,
          status: owners.length > 1 ? "ambiguous" : "missing",
        };
  const sum = (events: DemoCPAState["events"]) => ({
    usage_usd: events.reduce((total, e) => total + e.usage_usd, 0),
    request_count: events.length,
    token_count: events.reduce((total, e) => total + e.total_tokens, 0),
    unpriced_request_count: events.filter((e) => e.unpriced).length,
  });
  const events = state.cpa!.events.filter((e) => e.account_id === accountId);
  const quotaAvailable = !events.some((e) => e.unpriced);
  const members = pool.allocations.flatMap((allocation) => {
    const person = state.participants.find(
      (p) => p.id === allocation.participant_id,
    );
    if (!person) return [];
    const totals = sum(events.filter((e) => e.participant_id === person.id));
    const expected = allocation.share_percent * 20;
    const remaining = expected - totals.usage_usd;
    return [
      {
        ...totals,
        participant_id: person.id,
        participant_name: person.name,
        is_owner: person.is_owner,
        is_self: own != null && own.includes(person.id),
        share_percent: allocation.share_percent,
        quota_available: quotaAvailable,
        is_overused: quotaAvailable && remaining < 0,
        expected_entitlement_usd: quotaAvailable ? expected : null,
        consumed_entitlement_usd: quotaAvailable ? totals.usage_usd : null,
        remaining_entitlement_usd: quotaAvailable ? remaining : null,
        account_breakdowns: [
          {
            account_id: accountId,
            quota_available: quotaAvailable,
            quota_unavailable_reasons: quotaAvailable
              ? []
              : ["本周期存在未定价请求"],
            quota_as_of: state.clock,
            charged_percent: quotaAvailable ? totals.usage_usd / 20 : null,
            remaining_share_percent: quotaAvailable
              ? Math.max(0, remaining / 20)
              : null,
            usage_usd: totals.usage_usd,
            estimated_capacity_usd: quotaAvailable ? 2000 : null,
            expected_entitlement_usd: quotaAvailable ? expected : null,
            consumed_entitlement_usd: quotaAvailable ? totals.usage_usd : null,
            remaining_entitlement_usd: quotaAvailable ? remaining : null,
          },
        ],
      },
    ];
  });
  return {
    billing_summary: demoBilling(state, pool.id, members, accountId),
    weekly_distribution: [
      {
        account_id: accountId,
        account_name: account.name,
        started_at: new Date(
          Date.parse(state.clock) - 3 * 86400000,
        ).toISOString(),
        resets_at: new Date(
          Date.parse(state.clock) + 4 * 86400000,
        ).toISOString(),
        quota_as_of: state.clock,
        requests_as_of: events.at(-1)?.occurred_at ?? null,
        capacity_usd: 2000,
        capacity_estimate: {
          source: "particle_filter",
          capacity_usd: 2000,
          lower_usd: 1650,
          upper_usd: 2400,
          prior_only: false,
          as_of: state.clock,
        },
        coverage_complete: quotaAvailable,
        remaining_usd: quotaAvailable
          ? Math.max(0, 2000 - sum(events).usage_usd)
          : null,
        upstream_remaining_percent: 72,
        usage_usd: sum(events).usage_usd,
        unpriced_request_count: sum(events).unpriced_request_count,
        unattributed_usd: sum(events.filter((e) => e.participant_id == null))
          .usage_usd,
        other_members_usd: 0,
        members: members.map((m) => ({
          participant_id: m.participant_id,
          usage_usd: m.usage_usd,
          usage_percent: m.usage_usd / 20,
        })),
      },
    ],
    pool_id: pool.id,
    pool_name: pool.name,
    selected_account_id: accountId,
    partial_scope: false,
    members,
    accounts: [
      {
        ...sum(events),
        account_id: accountId,
        account_name: account.name,
        owner,
        selected: true,
        quota_as_of: state.clock,
        requests_as_of: events.at(-1)?.occurred_at ?? null,
        cycle_started_at: new Date(
          Date.parse(state.clock) - 3 * 86400000,
        ).toISOString(),
        resets_at: new Date(
          Date.parse(state.clock) + 4 * 86400000,
        ).toISOString(),
        coverage: { complete: true, uncertain_end: false, gaps: [] },
        quota_available: quotaAvailable,
        quota_unavailable_reasons: quotaAvailable
          ? []
          : ["本周期存在未定价请求"],
      },
    ],
    unattributed: sum(events.filter((e) => e.participant_id == null)),
    cost_estimate: true,
    enforcement_enabled: false,
    generated_at: state.clock,
    collector: {
      state: "connected",
      connected: true,
      stale: false,
      connected_at: state.clock,
      heartbeat_at: state.clock,
      last_message_at: state.clock,
      last_persisted_at: state.clock,
      pending_count: 0,
      last_error: "",
      last_error_at: null,
    },
  };
}

function demoBilling(
  state: DemoState,
  poolId: number,
  members: CPAPoolSummary["members"],
  accountId: number,
): CPABillingSummary {
  const config = state.cpa?.billingConfigs?.[poolId];
  const result: CPABillingSummary = {
    configured: !!config?.anchor_date,
    anchor_date: config?.anchor_date ?? null,
    timezone: config?.timezone ?? "Asia/Shanghai",
    started_at: null,
    ended_at: null,
    generated_at: state.clock,
    capacity_usd: null,
    actual_capacity_usd: null,
    future_capacity_usd: null,
    expired_usd: null,
    available_usd: null,
    usage_usd: 0,
    unattributed_usd: 0,
    other_members_usd: 0,
    unallocated_usd: null,
    reasons: [],
    cycles: [],
    members: [],
  };
  if (!result.configured) return result;
  // Synthetic four-week scenario for UI review; never presented as production facts.
  const start = Date.parse(state.clock) - 7 * 86400000;
  const end = start + 28 * 86400000;
  result.started_at = new Date(start).toISOString();
  result.ended_at = new Date(end).toISOString();
  result.capacity_usd = 4000;
  result.actual_capacity_usd = 2000;
  result.future_capacity_usd = 2000;
  result.available_usd = 3000;
  result.expired_usd = 0;
  result.usage_usd = 1000;
  result.unallocated_usd = 0;
  result.cycles = Array.from({ length: 4 }, (_, i) => ({
    account_id: accountId,
    account_name: "CPA 演示账号",
    started_at: new Date(start + i * 7 * 86400000).toISOString(),
    ended_at: new Date(start + (i + 1) * 7 * 86400000).toISOString(),
    kind: i === 0 ? "historical" : i === 1 ? "current" : "future",
    capacity_usd: 1000,
    full_capacity_usd: 1000,
    expired_usd: i === 0 ? 0 : null,
    quota_as_of: i < 2 ? state.clock : null,
    reasons: [],
  }));
  result.members = members.map((m, i) => {
    const spend = i === 0 ? 500 : 250;
    const entitlement = (4000 * (m.share_percent ?? 0)) / 100;
    const remaining = entitlement - spend;
    return {
      participant_id: m.participant_id,
      usage_usd: spend,
      usage_percent: spend / 40,
      entitlement_usd: entitlement,
      remaining_usd: remaining,
      recommended_usd: Math.max(0, remaining),
      recommended_percent: Math.max(0, remaining) / 30,
      completed_overuse_usd: Math.max(0, spend - entitlement / 4),
      projected_overuse: remaining < 0,
    };
  });
  return result;
}

export function demoCPAKeySeries(
  state: DemoState,
  accountId: number,
  days: number,
  precision: "raw" | "hour" | "day",
): CPAAPIKeyUsageSeries[] {
  if (!canRead(state, accountId)) return [];
  const own = ownIds(state);
  const cutoff = Date.parse(state.clock) - days * 86400000;
  const groups = new Map<number, CPAAPIKeyUsageSeries>();
  const buckets = new Map<string, CPAAPIKeyUsageSeries["points"][number]>();
  const format = new Intl.DateTimeFormat("sv-SE", {
    timeZone: String(state.settings.timezone),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  for (const event of state.cpa!.events) {
    if (
      event.account_id !== accountId ||
      Date.parse(event.occurred_at) < cutoff ||
      (own &&
        (event.participant_id == null || !own.includes(event.participant_id)))
    )
      continue;
    let series = groups.get(event.key_id);
    if (!series) {
      const key = state.cpa!.keys.keys.find((k) => k.id === event.key_id);
      series = {
        api_key_id: `demo-key-${event.key_id}`,
        api_key_name: `${key?.name || "API Key"} ····${event.api_key_hint}`,
        total_usage_usd: 0,
        request_count: 0,
        token_count: 0,
        unpriced_request_count: 0,
        points: [],
      };
      groups.set(event.key_id, series);
    }
    series.total_usage_usd += event.usage_usd;
    series.request_count++;
    series.token_count += event.total_tokens;
    series.unpriced_request_count += Number(event.unpriced);
    const local = format.format(new Date(event.occurred_at));
    const label =
      precision === "day"
        ? local.slice(0, 10)
        : precision === "hour"
          ? `${local.slice(0, 13)}:00`
          : local;
    const bucketId = `${event.key_id}:${precision === "raw" ? event.id : label}`;
    let point = buckets.get(bucketId);
    if (!point) {
      point = {
        observed_at: event.occurred_at,
        label,
        usage_usd: 0,
        request_count: 0,
        token_count: 0,
      };
      buckets.set(bucketId, point);
      series.points.push(point);
    }
    point.usage_usd += event.usage_usd;
    point.request_count++;
    point.token_count += event.total_tokens;
  }
  return [...groups.values()];
}

export function handleCPA({
  state,
  pathname,
  method,
  payload,
  url,
  ok,
  fail,
}: DemoRequestContext): Response | null {
  if (pathname === "settings/cpa-pricing") {
    if (!demoIdentity()?.is_staff) return fail("仅管理员可管理模型价格", 403);
    const accountId = url.searchParams.has("account_id")
      ? Number(url.searchParams.get("account_id"))
      : null;
    if (
      accountId != null &&
      !state.monitoredAccounts.some(
        (a) => a.id === accountId && a.provider === "cpa",
      )
    )
      return fail("CPA 账号不存在", 404);
    const inventory = demoPricingInventory(state, accountId);
    if (method === "GET") return ok(inventory);
    if (method !== "POST") return fail("不支持此操作", 405);
    const added: CPAPricingSync["added"] = [];
    const unresolved: CPAPricingSync["unresolved"] = [];
    // Deterministic public-catalog snapshot; demo mode never contacts a service.
    const catalog = {
      "gpt-6-astra": { input: "10", cached_input: "1", output: "50" },
      "gpt-5.4": { input: "2.5", cached_input: "0.25", output: "15" },
    };
    for (const row of inventory.models.filter((row) => row.missing)) {
      const price = catalog[row.model as keyof typeof catalog];
      if (price) {
        state.settings.cpa_model_pricing[row.model] = { ...price };
        added.push({
          model: row.model,
          source_model: `openai/${row.model}`,
          price,
        });
      } else
        unresolved.push({
          model: row.model,
          reason: "演示目录未收录此模型，请手动填写",
        });
    }
    repriceCPADemo(state);
    saveDemoState(state);
    return ok({ ...demoPricingInventory(state, accountId), added, unresolved });
  }
  if (!pathname.startsWith("cpa/")) return null;
  const cpa = state.cpa!;
  const admin = demoIdentity()?.is_staff;
  const own = ownIds(state);
  if (pathname === "cpa/billing-config") {
    if (!admin) return fail("仅管理员可修改", 403);
    if (method !== "PUT") return fail("不支持此操作", 405);
    const accountId = Number(url.searchParams.get("account_id"));
    const account = state.monitoredAccounts.find((a) => a.id === accountId);
    if (!account || !canRead(state, accountId))
      return fail("CPA 账号未授权", 403);
    const body = {
      anchor_date: payload.anchor_date ? String(payload.anchor_date) : null,
      timezone: String(payload.timezone),
    };
    cpa.billingConfigs ??= {};
    cpa.billingConfigs[account.pool_id] = body;
    saveDemoState(state);
    return ok(demoCPASummary(state, accountId));
  }
  if (pathname === "cpa/summary" || pathname === "cpa/requests") {
    const accountId = Number(url.searchParams.get("account_id"));
    if (!canRead(state, accountId)) return fail("CPA 账号未授权", 403);
    if (pathname === "cpa/summary") return ok(demoCPASummary(state, accountId));
    let events = cpa.events.filter(
      (e) =>
        e.account_id === accountId &&
        (own == null ||
          (e.participant_id != null && own.includes(e.participant_id))),
    );
    const hashes = new Set(events.map((e) => e.key_id));
    const keys = cpa.keys.keys
      .filter((k) => hashes.has(k.id))
      .map((k) => ({ id: k.id, name: k.name, hint: k.hint }));
    const models = [...new Set(events.map((e) => e.model))];
    const params = url.searchParams;
    if (params.get("key_id"))
      events = events.filter((e) => e.key_id === Number(params.get("key_id")));
    if (params.get("model"))
      events = events.filter((e) => e.model === params.get("model"));
    if (params.has("failed"))
      events = events.filter(
        (e) => e.failed === (params.get("failed") === "true"),
      );
    const endedAt = params.get("ended_at") || state.clock;
    const startedAt =
      params.get("started_at") ||
      new Date(
        Date.parse(endedAt) - Number(params.get("days") || 7) * 86400000,
      ).toISOString();
    if (
      !Number.isFinite(Date.parse(startedAt)) ||
      !Number.isFinite(Date.parse(endedAt)) ||
      Date.parse(startedAt) >= Date.parse(endedAt) ||
      Date.parse(endedAt) - Date.parse(startedAt) > 90 * 86400000
    )
      return fail("请求查询时间范围须大于零且不超过 90 天");
    events = events.filter(
      (e) => e.occurred_at >= startedAt && e.occurred_at < endedAt,
    );
    const sum = (
      field:
        | "input_tokens"
        | "cached_input_tokens"
        | "output_tokens"
        | "reasoning_tokens"
        | "total_tokens"
        | "usage_usd",
    ) => events.reduce((total, event) => total + event[field], 0);
    const average = (field: "latency_ms" | "ttft_ms") => {
      const observed = events.filter((e) => e[field] > 0);
      return observed.length
        ? observed.reduce((total, e) => total + e[field], 0) / observed.length
        : null;
    };
    const summary =
      params.get("include_summary") === "true"
        ? {
            request_count: events.length,
            failed_count: events.filter((e) => e.failed).length,
            input_tokens: sum("input_tokens"),
            cached_input_tokens: sum("cached_input_tokens"),
            output_tokens: sum("output_tokens"),
            reasoning_tokens: sum("reasoning_tokens"),
            total_tokens: sum("total_tokens"),
            usage_usd: sum("usage_usd"),
            unpriced_request_count: events.filter((e) => e.unpriced).length,
            average_latency_ms: average("latency_ms"),
            average_ttft_ms: average("ttft_ms"),
          }
        : null;
    events.sort((a, b) => b.occurred_at.localeCompare(a.occurred_at));
    const page = Math.max(1, Number(params.get("page") ?? 1));
    const size = Math.min(
      100,
      Math.max(1, Number(params.get("page_size") ?? 50)),
    );
    return ok({
      account_id: accountId,
      items: events.slice((page - 1) * size, page * size).map((event) => ({
        ...event,
        api_key_alias:
          event.api_key_alias ||
          cpa.keys.keys.find((key) => key.id === event.key_id)?.name ||
          "",
      })),
      total: events.length,
      summary,
      started_at: startedAt,
      ended_at: endedAt,
      page,
      page_size: size,
      keys,
      models,
      generated_at: state.clock,
      cost_estimate: true,
    });
  }
  if (!admin) return fail("仅管理员可管理 CPA Key", 403);
  if (pathname === "cpa/unassigned/preview" && method === "POST") {
    const accountId = Number(url.searchParams.get("account_id"));
    const summary = demoCPASummary(state, accountId);
    const owner = summary?.accounts[0]?.owner;
    if (owner?.status !== "active" || owner.participant_id == null)
      return fail("请先在参与者管理中设置唯一车主");
    const events = cpa.events.filter(
      (e) => e.account_id === accountId && e.participant_id == null,
    );
    if (!events.length) return fail("此范围没有未归属的已采集请求");
    const plan: DemoCPAState["claims"][number] = {
      id: `demo-${cpa.claims.length + 1}`,
      key_id: null,
      account_id: accountId,
      participant_id: owner.participant_id,
      started_at: summary!.accounts[0]!.cycle_started_at,
      ended_at: state.clock,
      expires_at: new Date(Date.now() + 900000).toISOString(),
      applied_at: null,
      revision: state.revision,
      historical_contract_policy:
        "仅认领已采集且尚无归属的请求，不补造断线数据或历史份额",
      accounts: [
        {
          account_id: accountId,
          account_name: summary!.accounts[0]!.account_name,
          request_count: events.length,
          token_count: events.reduce((sum, e) => sum + e.total_tokens, 0),
          usage_usd: events.reduce((sum, e) => sum + e.usage_usd, 0),
          unpriced_request_count: events.filter((e) => e.unpriced).length,
          coverage: summary!.accounts[0]!.coverage,
        },
      ],
    };
    cpa.claims.push(plan);
    saveDemoState(state);
    return ok(plan, 201);
  }
  if (pathname === "cpa/keys" && method === "GET") return ok(cpa.keys);
  if (pathname === "cpa/keys" && method === "POST") {
    const person = state.participants.find(
      (p) => p.id === Number(payload.participant_id),
    );
    if (!person) return fail("请选择参与者");
    const hash = String(payload.observed_hash ?? "");
    let key = cpa.keys.keys.find((k) => k.observed_hash === hash);
    if (key?.bindings.some((b) => !b.ended_at)) return fail("该 Key 已绑定");
    if (!key) {
      const observed = cpa.keys.unregistered.find(
        (k) => k.observed_hash === hash,
      );
      const id = Math.max(0, ...cpa.keys.keys.map((k) => k.id)) + 1;
      key = {
        id,
        name: String(payload.name ?? ""),
        hint: observed?.hint ?? String(payload.raw_key ?? "").slice(-4),
        observed_hash: hash || String(id).padStart(64, "0"),
        bindings: [],
      };
      cpa.keys.keys.push(key);
      cpa.keys.unregistered = cpa.keys.unregistered.filter(
        (k) => k.observed_hash !== hash,
      );
    }
    const binding = {
      id:
        Math.max(
          0,
          ...cpa.keys.keys.flatMap((k) => k.bindings.map((b) => b.id)),
        ) + 1,
      key_id: key.id,
      hint: key.hint,
      name: key.name,
      participant_id: person.id,
      participant_name: person.name,
      started_at: state.clock,
      ended_at: null,
    };
    key.bindings.push(binding);
    state.revision++;
    saveDemoState(state);
    return ok(binding, 201);
  }
  const bindingMatch = /^cpa\/bindings\/(\d+)$/.exec(pathname);
  if (bindingMatch && method === "PATCH") {
    const key = cpa.keys.keys.find((k) =>
      k.bindings.some((b) => b.id === Number(bindingMatch[1])),
    );
    if (!key) return fail("绑定不存在", 404);
    key.name = String(payload.name ?? "");
    for (const binding of key.bindings) binding.name = key.name;
    saveDemoState(state);
    return ok(key.bindings.find((b) => b.id === Number(bindingMatch[1])));
  }
  if (bindingMatch && method === "DELETE") {
    const binding = cpa.keys.keys
      .flatMap((k) => k.bindings)
      .find((b) => b.id === Number(bindingMatch[1]));
    if (!binding) return fail("绑定不存在", 404);
    binding.ended_at ??= state.clock;
    state.revision++;
    saveDemoState(state);
    return ok(binding);
  }
  if (pathname === "cpa/claims/preview" && method === "POST") {
    const start = String(payload.started_at);
    const end = String(payload.ended_at);
    const key = cpa.keys.keys.find((k) => k.id === Number(payload.key_id));
    if (!key || !(Date.parse(start) < Date.parse(end)))
      return fail("请选择有效时间范围");
    if (
      key.bindings.some(
        (b) =>
          Date.parse(b.started_at) < Date.parse(end) &&
          (!b.ended_at || Date.parse(b.ended_at) > Date.parse(start)),
      )
    )
      return fail("此范围已有归属");
    const events = cpa.events.filter(
      (e) =>
        e.key_id === key.id &&
        Date.parse(e.occurred_at) >= Date.parse(start) &&
        Date.parse(e.occurred_at) < Date.parse(end),
    );
    if (!events.length) return fail("此范围没有已采集请求");
    const plan = {
      id: `demo-${cpa.claims.length + 1}`,
      key_id: key.id,
      participant_id: Number(payload.participant_id),
      started_at: start,
      ended_at: end,
      expires_at: new Date(Date.now() + 900000).toISOString(),
      applied_at: null,
      revision: state.revision,
      historical_contract_policy: "演示：缺少历史份额时不补造历史权益。",
      accounts: [
        {
          account_id: 3,
          account_name: "CPA 拼车账号",
          usage_usd: events.reduce((v, e) => v + e.usage_usd, 0),
          request_count: events.length,
          token_count: events.reduce((v, e) => v + e.total_tokens, 0),
          unpriced_request_count: 0,
          coverage: { complete: true, uncertain_end: false, gaps: [] },
        },
      ],
    };
    cpa.claims.push(plan);
    saveDemoState(state);
    return ok(plan, 201);
  }
  const claimMatch = /^cpa\/claims\/([^/]+)\/apply$/.exec(pathname);
  if (claimMatch && method === "POST") {
    const plan = cpa.claims.find((p) => p.id === claimMatch[1]);
    if (!plan) return fail("预览不存在", 404);
    if (plan.applied_at) return ok(plan);
    if (
      plan.revision !== state.revision ||
      Date.parse(plan.expires_at) <= Date.now()
    )
      return fail("预览已变化，请重新预览");
    if (plan.account_id != null) {
      for (const item of cpa.events) {
        if (
          item.account_id === plan.account_id &&
          item.participant_id == null &&
          item.occurred_at >= plan.started_at &&
          item.occurred_at < plan.ended_at
        )
          item.participant_id = plan.participant_id;
      }
      plan.applied_at = state.clock;
      state.revision++;
      saveDemoState(state);
      return ok(plan);
    }
    for (const item of cpa.events)
      if (
        item.key_id === plan.key_id &&
        Date.parse(item.occurred_at) >= Date.parse(plan.started_at) &&
        Date.parse(item.occurred_at) < Date.parse(plan.ended_at)
      )
        item.participant_id = plan.participant_id;
    const key = cpa.keys.keys.find((k) => k.id === plan.key_id)!;
    key.bindings.push({
      id:
        Math.max(
          0,
          ...cpa.keys.keys.flatMap((k) => k.bindings.map((b) => b.id)),
        ) + 1,
      key_id: key.id,
      hint: key.hint,
      name: key.name,
      participant_id: plan.participant_id,
      participant_name:
        state.participants.find((p) => p.id === plan.participant_id)?.name ??
        "",
      started_at: plan.started_at,
      ended_at: plan.ended_at,
    });
    plan.applied_at = state.clock;
    state.revision++;
    saveDemoState(state);
    return ok(plan);
  }
  return fail("CPA 演示接口不存在", 404);
}
