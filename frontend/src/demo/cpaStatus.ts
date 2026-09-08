import type { CPAQuotaDetail, CPAResetPreview } from "@/types/accounts";
import type { DemoState } from "./state";
import { demoIdentity, saveDemoState } from "./state";
import type { DemoRequestContext } from "./backend";

export function demoCPAStatus(
  state: DemoState,
  accountId: number,
): CPAQuotaDetail {
  const cpa = state.cpa!;
  cpa.quotaStatuses ??= {};
  const now = Date.parse(state.clock),
    start = now - 5 * 3600000,
    end = start + 7 * 86400000;
  const iso = (value: number) => new Date(value).toISOString();
  const current = {
    request_count: 947,
    token_count: 127_700_000,
    usage_usd: 120.18,
    success_rate: 99.89,
    unpriced_request_count: 0,
  };
  const previous = {
    request_count: 1893,
    token_count: 228_700_000,
    usage_usd: 336.35,
    success_rate: 99.95,
    unpriced_request_count: 0,
  };
  const result = (cpa.quotaStatuses[accountId] ??= {
    totals: {
      started_at: iso(start - 86400000),
      ended_at: state.clock,
      coverage_complete: true,
      metrics: {
        request_count: 2840,
        token_count: 356_400_000,
        usage_usd: 456.53,
        success_rate: 99.93,
        unpriced_request_count: 0,
      },
    },
    windows: [
      {
        id: "standard-week",
        label: "周限额",
        used_percent: 11,
        reset_at: iso(end),
        updated_at: state.clock,
        source: "主动 API 查询",
        boundary: "provider",
        current: {
          started_at: iso(start),
          ended_at: iso(end),
          coverage_complete: true,
          metrics: current,
        },
        previous: {
          started_at: iso(start - 7 * 86400000),
          ended_at: iso(start),
          coverage_complete: true,
          metrics: previous,
        },
        prediction: null,
        notice:
          "用量为该时间段的账号合计；附加限额的模型范围不明确，暂不预测。",
      },
      {
        id: "spark-week",
        label: "GPT-5.3-Codex-Spark 周限额",
        used_percent: 0,
        reset_at: iso(end + 6000000),
        updated_at: state.clock,
        source: "主动 API 查询",
        boundary: "provider",
        current: null,
        previous: null,
        prediction: null,
        notice: "上游未提供完整模型范围，未计算该窗口用量。",
      },
    ],
    reset: { available_count: 1, can_reset: false, history: [] },
  });
  const identity = demoIdentity();
  const account = state.monitoredAccounts.find((a) => a.id === accountId);
  const pool = state.quotaPools.find((p) => p.id === account?.pool_id);
  const owners = state.participants.filter(
    (p) =>
      p.enabled &&
      p.is_owner &&
      pool?.allocations.some((a) => a.participant_id === p.id),
  );
  const user = state.systemUsers.find((u) => u.username === identity?.username);
  result.reset.can_reset = Boolean(
    identity?.is_staff ||
    (owners.length === 1 &&
      user?.participant_ids.includes(owners[0]!.id) &&
      user.account_ids.includes(accountId)),
  );
  return result;
}
export function handleCPAStatus(context: DemoRequestContext): Response | null {
  const { pathname, state, method, payload, ok, fail } = context;
  const match =
    /^account-status\/cpa\/(\d+)\/(reset-preview|resets\/([^/]+)\/confirm)$/.exec(
      pathname,
    );
  if (!match || method !== "POST") return null;
  const accountId = Number(match[1]);
  const account = state.monitoredAccounts.find(
    (a) => a.id === accountId && a.provider === "cpa",
  );
  if (!account) return fail("账号不存在", 404);
  const detail = demoCPAStatus(state, accountId);
  if (!detail.reset.can_reset) return fail("仅管理员或车主可以重置额度", 403);
  const cpa = state.cpa!;
  cpa.resetPlans ??= [];
  if (match[2] === "reset-preview") {
    if (!detail.reset.available_count) return fail("没有可用重置次数");
    const plan: CPAResetPreview = {
      id: `demo-reset-${cpa.resetPlans.length + 1}`,
      account_id: accountId,
      account_name: account.name,
      available_count: detail.reset.available_count,
      status: "pending",
      expires_at: new Date(Date.now() + 300000).toISOString(),
    };
    cpa.resetPlans.push(plan);
    saveDemoState(state);
    return ok(plan, 201);
  }
  const plan = cpa.resetPlans.find(
    (p) => p.id === match[3] && p.account_id === accountId,
  );
  if (!plan || payload.confirmed !== true) return fail("请完成二次确认");
  if (plan.status === "succeeded") return ok(plan);
  if (
    Date.parse(plan.expires_at) <= Date.now() ||
    !detail.reset.available_count
  )
    return fail("重置确认已过期");
  detail.reset.available_count--;
  detail.reset.history.unshift({
    id: plan.id,
    status: "succeeded",
    created_at: state.clock,
    finished_at: state.clock,
  });
  for (const window of detail.windows) {
    window.used_percent = 0;
    window.prediction = null;
    if (window.current)
      window.current.metrics = {
        request_count: 0,
        token_count: 0,
        usage_usd: 0,
        success_rate: null,
        unpriced_request_count: 0,
      };
  }
  plan.status = "succeeded";
  saveDemoState(state);
  return ok(plan);
}
