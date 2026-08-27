import type { MonitoredAccount } from "@/types/accounts";
import type {
  AppSettingsData,
  FastCorrectionRule,
  HistoricalRebuildPlan,
} from "@/types/settings";

import type { DemoRequestContext } from "../backend";
import {
  aggregateParticipant,
  resetDemoState,
  saveDemoState,
  type DemoState,
} from "../state";
import { participantBreakdowns } from "./participants";
import { accountNames } from "./systemUsers";
function quotaProfile(value: unknown): MonitoredAccount["quota_profile"] {
  return value === "plus" || value === "pro_5x" || value === "pro_20x"
    ? value
    : "auto";
}

function effectiveQuotaProfile(
  account: Pick<MonitoredAccount, "quota_profile" | "detected_plan_type">,
): MonitoredAccount["effective_quota_profile"] {
  if (account.quota_profile !== "auto") return account.quota_profile;
  return account.detected_plan_type === "plus" ? "plus" : "pro_20x";
}

function applyCapacityRange(
  account: MonitoredAccount,
  payload: Record<string, unknown>,
) {
  if (
    payload.capacity_min_usd_override !== undefined ||
    payload.capacity_max_usd_override !== undefined
  ) {
    const rawMin = payload.capacity_min_usd_override;
    const rawMax = payload.capacity_max_usd_override;
    account.capacity_min_usd_override = rawMin == null ? null : Number(rawMin);
    account.capacity_max_usd_override = rawMax == null ? null : Number(rawMax);
  }
  const defaults = {
    plus: { min: 100, max: 200 },
    pro_5x: { min: 500, max: 1500 },
    pro_20x: { min: 1400, max: 4000 },
  }[account.effective_quota_profile];
  account.capacity_min_usd = account.capacity_min_usd_override ?? defaults.min;
  account.capacity_max_usd = account.capacity_max_usd_override ?? defaults.max;
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

export function handleSettings({
  method,
  pathname,
  payload,
  state,
  ok,
  fail,
}: DemoRequestContext): Response | null {
  if (method === "GET" && pathname === "settings/monitored-accounts") {
    return ok(state.monitoredAccounts);
  }
  if (method === "POST" && pathname === "settings/monitored-accounts") {
    const externalAccountId = Number(payload.external_account_id);
    if (
      state.monitoredAccounts.some(
        (account) => account.external_account_id === externalAccountId,
      )
    ) {
      return fail("该上游账号已经在监控列表中", 400);
    }
    const accountId =
      Math.max(0, ...state.monitoredAccounts.map((item) => item.id)) + 1;
    const poolId = state.nextPoolId++;
    const accountName = String(
      payload.name ?? `OpenAI 账号 ${externalAccountId}`,
    );
    const account: MonitoredAccount = {
      id: accountId,
      pool_id: poolId,
      external_account_id: externalAccountId,
      name: accountName,
      enabled: payload.enabled !== false,
      quota_query_mode:
        payload.quota_query_mode === "direct" ? "direct" : "passive",
      quota_profile: quotaProfile(payload.quota_profile),
      detected_plan_type: "",
      effective_quota_profile: "pro_20x",
      capacity_min_usd_override: null,
      capacity_max_usd_override: null,
      capacity_min_usd: 1400,
      capacity_max_usd: 4000,
      last_local_check_at: null,
      last_upstream_check_at: null,
      last_success_at: null,
      next_local_check_at: null,
      last_error: "",
    };
    account.effective_quota_profile = effectiveQuotaProfile(account);
    applyCapacityRange(account, payload);
    state.monitoredAccounts.push(account);
    state.quotaPools.push({
      id: poolId,
      name: `${accountName} 独立池`,
      contract_revision: 1,
      account_ids: [accountId],
      allocations: [],
      total_share_percent: 0,
    });
    for (const participant of state.participants) {
      participant.account_breakdowns = participantBreakdowns(
        state,
        participant.id,
        participant.account_breakdowns,
      );
      aggregateParticipant(participant);
    }
    saveDemoState(state);
    return ok(account, 201);
  }
  const monitoredAccountMatch = /^settings\/monitored-accounts\/(\d+)$/.exec(
    pathname,
  );
  if (monitoredAccountMatch && method === "PUT") {
    const account = state.monitoredAccounts.find(
      (item) => item.id === Number(monitoredAccountMatch[1]),
    );
    if (!account) return fail("监控账号不存在", 404);
    account.external_account_id = Number(
      payload.external_account_id ?? account.external_account_id,
    );
    account.name = String(payload.name ?? account.name);
    account.enabled =
      payload.enabled === undefined
        ? account.enabled
        : payload.enabled !== false;
    account.quota_query_mode =
      payload.quota_query_mode === "direct" ? "direct" : "passive";
    account.quota_profile = quotaProfile(
      payload.quota_profile ?? account.quota_profile,
    );
    account.effective_quota_profile = effectiveQuotaProfile(account);
    applyCapacityRange(account, payload);
    for (const participant of state.participants) {
      participant.account_breakdowns = participantBreakdowns(
        state,
        participant.id,
        participant.account_breakdowns,
      );
      aggregateParticipant(participant);
    }
    saveDemoState(state);
    return ok(account);
  }
  if (monitoredAccountMatch && method === "DELETE") {
    const accountId = Number(monitoredAccountMatch[1]);
    state.monitoredAccounts = state.monitoredAccounts.filter(
      (item) => item.id !== accountId,
    );
    for (const user of state.systemUsers) {
      user.account_ids = user.account_ids.filter((item) => item !== accountId);
      user.account_names = accountNames(state, user.account_ids);
    }
    state.quotaPools = state.quotaPools.flatMap((pool) => {
      const remainingAccountIds = pool.account_ids.filter(
        (item) => item !== accountId,
      );
      return remainingAccountIds.length
        ? [
            {
              ...pool,
              contract_revision: pool.contract_revision + 1,
              account_ids: remainingAccountIds,
            },
          ]
        : [];
    });
    for (const participant of state.participants) {
      participant.pool_allocations = participant.pool_allocations.flatMap(
        (allocation) => {
          const pool = state.quotaPools.find(
            (item) => item.id === allocation.pool_id,
          );
          return pool
            ? [
                {
                  ...allocation,
                  account_ids: [...pool.account_ids],
                  account_count: pool.account_ids.length,
                },
              ]
            : [];
        },
      );
      participant.account_breakdowns = participantBreakdowns(
        state,
        participant.id,
        participant.account_breakdowns.filter(
          (item) => item.account_id !== accountId,
        ),
      );
      aggregateParticipant(participant);
    }
    saveDemoState(state);
    return ok();
  }
  if (method === "GET" && pathname === "settings") {
    return ok(state.settings satisfies AppSettingsData);
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
        state.settings[key] = value as
          | string
          | number
          | boolean
          | null
          | FastCorrectionRule[];
      }
    }
    saveDemoState(state);
    return ok(state.settings);
  }
  if (method === "POST" && pathname === "settings/openai-accounts") {
    return ok([
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
    return ok({
      demo: true,
      connected: pathname.endsWith("sub2api"),
      sent: false,
    });
  }
  if (pathname === "settings/readonly-api-key" && method === "POST") {
    const generated = {
      api_key: "demo_api_key_not_a_secret",
      hint: "demo_...cret",
      created_at: state.clock,
    };
    state.settings.readonly_api_key_configured = true;
    state.settings.readonly_api_key_hint = generated.hint;
    state.settings.readonly_api_key_created_at = generated.created_at;
    saveDemoState(state);
    return ok(generated);
  }
  if (pathname === "settings/readonly-api-key" && method === "DELETE") {
    state.settings.readonly_api_key_configured = false;
    state.settings.readonly_api_key_hint = "";
    state.settings.readonly_api_key_created_at = null;
    saveDemoState(state);
    return ok({ revoked: true });
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
      return fail("必须指定有效的监控账号", 400);
    }
    const plan = createPlan(state, Number(payload.account_id));
    saveDemoState(state);
    return ok(plan, 201);
  }
  const planMatch =
    /^settings\/data-maintenance\/history-rebuild-plans\/([^/]+)(?:\/(apply))?$/.exec(
      pathname,
    );
  if (planMatch) {
    const plan = state.plans.find((item) => item.id === planMatch[1]);
    if (!plan) return fail("维护计划不存在", 404);
    if (method === "GET" && !planMatch[2]) return ok(plan);
    if (method === "POST" && planMatch[2] === "apply") {
      const digest = payload.digest;
      if (typeof digest !== "string" || !digest) {
        return fail("apply 必须提交计划 digest", 400);
      }
      if (plan.state === "applied") {
        return digest === plan.digest
          ? ok(plan)
          : fail("计划已经应用且 digest 不匹配", 409);
      }
      if (plan.state !== "ready" || !plan.safe_to_apply) {
        return fail("计划当前不可应用", 409);
      }
      if (digest !== plan.digest) {
        return fail("计划 digest 不匹配", 409);
      }
      if (Date.parse(plan.expires_at) <= Date.now()) {
        plan.state = "stale";
        plan.safe_to_apply = false;
        saveDemoState(state);
        return fail("计划已过期，请重新创建", 409);
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
      return ok(plan);
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
    return ok({ demo_reset: true });
  }
  return null;
}
