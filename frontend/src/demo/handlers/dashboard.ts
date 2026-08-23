import type {
  QuotaAllocationData,
  QuotaAllocationWritePool,
  QuotaPoolAllocation,
} from "@/types";

import type { DemoRequestContext } from "../backend";
import {
  aggregateParticipant,
  dashboardData,
  saveDemoState,
  type DemoState,
} from "../state";
import { participantBreakdowns } from "./participants";

function quotaAllocationData(state: DemoState): QuotaAllocationData {
  return {
    accounts: state.monitoredAccounts,
    participants: state.participants.map((participant) => ({
      id: participant.id,
      name: participant.name,
      sub2api_user_id: participant.sub2api_user_id,
      sub2api_username: participant.sub2api_username,
      sub2api_email: participant.sub2api_email,
      sub2api_identity: participant.sub2api_identity,
      is_owner: participant.is_owner,
      enabled: participant.enabled,
    })),
    pools: state.quotaPools,
  };
}

function quotaPoolSignature(pool: {
  name: string;
  account_ids: number[];
  allocations: Array<{ participant_id: number; share_percent: number }>;
}) {
  const accountIds = [...pool.account_ids].sort((left, right) => left - right);
  const allocations = [...pool.allocations].sort(
    (left, right) => left.participant_id - right.participant_id,
  );
  return JSON.stringify([pool.name, accountIds, allocations]);
}

function applyQuotaAllocation(state: DemoState, value: unknown): string | null {
  if (!Array.isArray(value)) return "额度池列表格式无效";

  const accountIds = new Set(state.monitoredAccounts.map((item) => item.id));
  const participantIds = new Set(state.participants.map((item) => item.id));
  const existingPools = new Map(
    state.quotaPools.map((pool) => [pool.id, pool]),
  );
  const seenAccountIds = new Set<number>();
  const seenPoolIds = new Set<number>();
  const specs: QuotaAllocationWritePool[] = [];

  for (const [index, rawPool] of value.entries()) {
    if (!rawPool || typeof rawPool !== "object" || Array.isArray(rawPool)) {
      return `第 ${index + 1} 个额度池格式无效`;
    }
    const raw = rawPool as Record<string, unknown>;
    const rawAccountIds = raw.account_ids;
    const rawAllocations = raw.allocations ?? [];
    if (!Array.isArray(rawAccountIds) || !rawAccountIds.length) {
      return `第 ${index + 1} 个额度池必须包含账号`;
    }
    if (!Array.isArray(rawAllocations)) {
      return `第 ${index + 1} 个额度池份额格式无效`;
    }
    const poolAccountIds = rawAccountIds.map(Number);
    if (
      poolAccountIds.some(
        (accountId) =>
          !Number.isInteger(accountId) || !accountIds.has(accountId),
      )
    ) {
      return `第 ${index + 1} 个额度池包含未知账号`;
    }
    if (new Set(poolAccountIds).size !== poolAccountIds.length) {
      return `第 ${index + 1} 个额度池包含重复账号`;
    }
    for (const accountId of poolAccountIds) {
      if (seenAccountIds.has(accountId)) return "同一账号不能属于多个额度池";
      seenAccountIds.add(accountId);
    }

    const poolId = raw.id === undefined ? undefined : Number(raw.id);
    if (
      poolId !== undefined &&
      (!Number.isInteger(poolId) ||
        !existingPools.has(poolId) ||
        seenPoolIds.has(poolId))
    ) {
      return `第 ${index + 1} 个额度池 ID 无效`;
    }
    if (poolId !== undefined) seenPoolIds.add(poolId);

    const allocations: QuotaAllocationWritePool["allocations"] = [];
    const allocatedParticipantIds = new Set<number>();
    let total = 0;
    for (const rawAllocation of rawAllocations) {
      if (
        !rawAllocation ||
        typeof rawAllocation !== "object" ||
        Array.isArray(rawAllocation)
      ) {
        return `第 ${index + 1} 个额度池包含无效份额`;
      }
      const row = rawAllocation as Record<string, unknown>;
      const participantId = Number(row.participant_id);
      const sharePercent = Number(row.share_percent);
      if (
        !Number.isInteger(participantId) ||
        !participantIds.has(participantId) ||
        allocatedParticipantIds.has(participantId)
      ) {
        return `第 ${index + 1} 个额度池包含无效参与者`;
      }
      if (
        !Number.isFinite(sharePercent) ||
        sharePercent < 0 ||
        sharePercent > 100
      ) {
        return `第 ${index + 1} 个额度池包含无效百分比`;
      }
      allocatedParticipantIds.add(participantId);
      total += sharePercent;
      if (sharePercent > 0) {
        allocations.push({
          participant_id: participantId,
          share_percent: sharePercent,
        });
      }
    }
    if (total > 100) return `第 ${index + 1} 个额度池份额不能超过 100%`;
    specs.push({
      ...(poolId === undefined ? {} : { id: poolId }),
      name:
        typeof raw.name === "string" && raw.name.trim()
          ? raw.name.trim()
          : poolAccountIds.length === 1
            ? `${state.monitoredAccounts.find((item) => item.id === poolAccountIds[0])?.name ?? "账号"} 独立池`
            : `混池 ${index + 1}`,
      account_ids: poolAccountIds,
      allocations,
    });
  }

  if (
    seenAccountIds.size !== accountIds.size ||
    [...accountIds].some((accountId) => !seenAccountIds.has(accountId))
  ) {
    return "每个账号必须且只能属于一个额度池";
  }

  const nextPools: QuotaPoolAllocation[] = specs.map((spec) => {
    const previous =
      spec.id === undefined ? undefined : existingPools.get(spec.id);
    const id = spec.id ?? state.nextPoolId++;
    const contractRevision =
      previous && quotaPoolSignature(previous) === quotaPoolSignature(spec)
        ? previous.contract_revision
        : (previous?.contract_revision ?? 0) + 1;
    return {
      id,
      name: spec.name,
      contract_revision: contractRevision,
      account_ids: spec.account_ids,
      allocations: spec.allocations,
      total_share_percent: spec.allocations.reduce(
        (sum, allocation) => sum + allocation.share_percent,
        0,
      ),
    };
  });

  state.quotaPools = nextPools;
  for (const pool of nextPools) {
    for (const accountId of pool.account_ids) {
      const account = state.monitoredAccounts.find(
        (item) => item.id === accountId,
      );
      if (account) account.pool_id = pool.id;
    }
  }
  for (const participant of state.participants) {
    participant.pool_allocations = nextPools.flatMap((pool) => {
      const allocation = pool.allocations.find(
        (item) => item.participant_id === participant.id,
      );
      return allocation
        ? [
            {
              pool_id: pool.id,
              pool_name: pool.name,
              share_percent: allocation.share_percent,
              account_ids: [...pool.account_ids],
              account_count: pool.account_ids.length,
            },
          ]
        : [];
    });
    participant.account_breakdowns = participantBreakdowns(
      state,
      participant.id,
      participant.account_breakdowns,
    );
    aggregateParticipant(participant);
  }
  return null;
}

export function handleDashboard({
  method,
  pathname,
  payload,
  state,
  url,
  ok,
  fail,
}: DemoRequestContext): Response | null {
  if (method === "GET" && pathname === "dashboard") {
    const accountId = Number(url.searchParams.get("account_id"));
    return ok(dashboardData(state, accountId));
  }
  if (pathname === "monitor/run" && method === "GET") {
    const enabledAccounts = state.monitoredAccounts.filter(
      (account) => account.enabled,
    );
    const nextChecks = enabledAccounts.flatMap((account) =>
      account.next_local_check_at ? [account.next_local_check_at] : [],
    );
    return ok({
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
    return ok({ scheduled: true });
  }
  const applyRecommendation =
    /^dashboard\/participants\/(\d+)\/apply-recommendation$/.exec(pathname);
  if (method === "POST" && applyRecommendation) {
    const participant = state.participants.find(
      (item) => item.id === Number(applyRecommendation[1]),
    );
    if (participant?.snapshot?.recommended_balance_usd == null) {
      return fail("该参与者暂无可应用建议", 409);
    }
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
    return ok({ applied: true });
  }
  if (method === "GET" && pathname === "quota-allocation") {
    return ok(quotaAllocationData(state));
  }
  if (method === "PUT" && pathname === "quota-allocation") {
    const validationError = applyQuotaAllocation(state, payload.pools);
    if (validationError) return fail(validationError);
    saveDemoState(state);
    return ok(quotaAllocationData(state));
  }
  return null;
}
