import type { Participant, Snapshot } from "@/types/participants";

export function rounded(value: number, digits = 3): number {
  return Number(value.toFixed(digits));
}

export function snapshot(
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

export function aggregateParticipant(participant: Participant): void {
  const breakdowns = participant.account_breakdowns.filter(
    (item) =>
      item.account_enabled && item.allocated && item.contract_share_percent > 0,
  );
  if (!breakdowns.length) {
    participant.snapshot = null;
    return;
  }
  const complete = breakdowns.every((item) => item.snapshot !== null);
  const sources = breakdowns.map((breakdown) => {
    const sourceSnapshot = breakdown.snapshot;
    const charged = sourceSnapshot?.charged_cycle_percent ?? 0;
    const chargedLower = sourceSnapshot?.charged_percent_lower ?? charged;
    const chargedUpper = sourceSnapshot?.charged_percent_upper ?? charged;
    const selected = sourceSnapshot?.selected_cost ?? 0;
    const capacity = charged > 0 ? (selected * 100) / charged : 440;
    const contractShare = breakdown.contract_share_percent;
    const expectedEntitlement = sourceSnapshot
      ? (contractShare * capacity) / 100
      : null;
    const consumedEntitlement = sourceSnapshot
      ? (charged * capacity) / 100
      : null;
    const remainingEntitlement =
      expectedEntitlement != null && consumedEntitlement != null
        ? expectedEntitlement - consumedEntitlement
        : null;
    return {
      account_id: breakdown.account_id,
      external_account_id: breakdown.external_account_id,
      account_name: breakdown.account_name,
      pool_id: breakdown.pool_id,
      pool_name: breakdown.pool_name,
      pool_contract_revision: sourceSnapshot?.pool_contract_revision ?? 1,
      contract_share_percent: contractShare,
      snapshot: sourceSnapshot,
      net_position_usd: sourceSnapshot
        ? ((contractShare - charged) * capacity) / 100
        : null,
      net_position_min_usd: sourceSnapshot
        ? ((contractShare - chargedUpper) * capacity) / 100
        : null,
      net_position_max_usd: sourceSnapshot
        ? ((contractShare - chargedLower) * capacity) / 100
        : null,
      estimated_capacity_usd: sourceSnapshot ? capacity : null,
      expected_entitlement_usd: expectedEntitlement,
      consumed_entitlement_usd: consumedEntitlement,
      remaining_entitlement_usd: remainingEntitlement,
      entitlement_usage_percent:
        expectedEntitlement != null &&
        expectedEntitlement > 0 &&
        consumedEntitlement != null
          ? (consumedEntitlement * 100) / expectedEntitlement
          : sourceSnapshot
            ? 0
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
  const expectedEntitlement = sources.reduce(
    (total, item) => total + (item.expected_entitlement_usd ?? 0),
    0,
  );
  const consumedEntitlement = sources.reduce(
    (total, item) => total + (item.consumed_entitlement_usd ?? 0),
    0,
  );
  const remainingEntitlement = expectedEntitlement - consumedEntitlement;
  const entitlementUsagePercent =
    expectedEntitlement > 0
      ? (consumedEntitlement * 100) / expectedEntitlement
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
    pool_allocations: participant.pool_allocations,
    selected_cost: selectedCost,
    charged_cycle_percent: charged,
    expected_entitlement_usd: complete ? rounded(expectedEntitlement, 6) : null,
    consumed_entitlement_usd: complete ? rounded(consumedEntitlement, 6) : null,
    remaining_entitlement_usd: complete
      ? rounded(remainingEntitlement, 6)
      : null,
    entitlement_usage_percent: complete
      ? rounded(entitlementUsagePercent, 4)
      : null,
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
    pool_count: new Set(breakdowns.map((item) => item.pool_id)).size,
    reason: !complete
      ? "等待所有已分配账号产生当前用户的可用观测"
      : needsUpdate
        ? "全局余额与各额度池剩余权益区间差异较大"
        : "全局余额处于各额度池建议区间",
    allocation_model: "partitioned_pool_sum",
    sources: sources.map(({ capacity: _capacity, ...source }) => source),
  };
}
