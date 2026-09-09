import type { DemoRequestContext } from "../backend";
import type { DemoState } from "../state";
import { aggregateParticipant, demoIdentity, saveDemoState } from "../state";
import type { BurstCycle, TemporaryBurstData } from "@/types/temporaryBurst";

export function refreshDemoBurst(state: DemoState, apply = false) {
  const mode = state.temporaryBurst;
  if (!mode) return;
  const now = Date.parse(state.clock);
  if (mode.active && mode.expires_at && now >= Date.parse(mode.expires_at)) {
    mode.active = false;
    mode.ended_at = state.clock;
  }
  const dueCycles = mode.cycles.filter(
    (cycle) => !cycle.settled_at && now >= Date.parse(cycle.resets_at),
  );
  for (const cycle of dueCycles) {
    const usage = cycle.members.map(
      (member) =>
        state.participants
          .find((p) => p.id === member.participant_id)
          ?.account_breakdowns.find(
            (item) => item.account_id === cycle.account_id,
          )?.snapshot?.charged_cycle_percent ?? 0,
    );
    const rights = cycle.members.map(
      (member) => Number(member.base_share) + Number(member.opening_adjustment),
    );
    const unused = rights.map((right, index) =>
      Math.max(0, right - usage[index]!),
    );
    const excess = rights.map((right, index) =>
      Math.max(0, usage[index]! - right),
    );
    const spare = unused.reduce((a, b) => a + b, 0);
    const over = excess.reduce((a, b) => a + b, 0);
    const total = Math.min(spare, over);
    cycle.settlement = cycle.members.map((member, index) => ({
      ...member,
      effective_share: String(rights[index]),
      used_percent: String(usage[index]),
      next_adjustment: String(
        (spare ? (total * unused[index]!) / spare : 0) -
          (over ? (total * excess[index]!) / over : 0),
      ),
    }));
    cycle.settled_at = cycle.evidence_at = state.clock;
    if (total > 0)
      mode.cycles.push({
        ...cycle,
        is_burst_cycle: false,
        resets_at: new Date(
          Date.parse(cycle.resets_at) + 7 * 86400000,
        ).toISOString(),
        settled_at: null,
        evidence_at: null,
        settlement: [],
        members: cycle.settlement.map((row) => ({
          ...row,
          opening_adjustment: row.next_adjustment!,
          used_percent: undefined,
          next_adjustment: undefined,
          effective_share: undefined,
        })),
      });
    for (const person of state.participants) {
      const snapshot = person.account_breakdowns.find(
        (item) => item.account_id === cycle.account_id,
      )?.snapshot;
      if (snapshot) {
        snapshot.charged_cycle_percent =
          snapshot.charged_percent_lower =
          snapshot.charged_percent_upper =
            0;
        snapshot.selected_cost = 0;
        snapshot.recommendation_applied = false;
      }
    }
  }
  mode.auto_apply = state.settings.auto_apply_recommendations;
  mode.monitoring_enabled = state.settings.monitoring_enabled;
  mode.can_start =
    !mode.active &&
    !mode.cycles.some((cycle) => cycle.is_burst_cycle && !cycle.settled_at);
  for (const person of state.participants) {
    const adjustments: Record<number, number> = {};
    for (const cycle of mode.cycles.filter((row) => !row.settled_at)) {
      const member = cycle.members.find(
        (row) =>
          row.participant_id === person.id &&
          row.user_id === person.sub2api_user_id,
      );
      if (member)
        adjustments[cycle.account_id] = Number(member.opening_adjustment);
    }
    aggregateParticipant(person, adjustments);
    const snapshot = person.snapshot;
    if (!snapshot || !person.enabled) continue;
    snapshot.temporary_burst = mode.active;
    snapshot.temporary_burst_expires_at = mode.active ? mode.expires_at : null;
    if (mode.active) {
      snapshot.recommended_balance_usd =
        snapshot.recommended_balance_min_usd =
        snapshot.recommended_balance_max_usd =
          9999;
      snapshot.balance_difference_usd = 9999 - (person.latest_balance_usd ?? 0);
      snapshot.needs_manual_update = person.latest_balance_usd !== 9999;
      snapshot.recommendation_applied = !snapshot.needs_manual_update;
      snapshot.reason = "临时爽蹬：合成演示余额统一建议 9999，不访问真实上游";
    }
    if (apply && mode.auto_apply && snapshot.needs_manual_update) {
      person.latest_balance_usd = snapshot.current_balance_usd =
        snapshot.recommended_balance_usd;
      snapshot.balance_difference_usd = 0;
      snapshot.needs_manual_update = false;
      snapshot.recommendation_applied = true;
    }
  }
  saveDemoState(state);
}

export function handleTemporaryBurst({
  pathname,
  method,
  payload,
  state,
  ok,
  fail,
}: DemoRequestContext): Response | null {
  if (pathname !== "dashboard/temporary-burst") return null;
  if (!demoIdentity()?.is_staff) return fail("没有管理员权限", 403);
  refreshDemoBurst(state);
  const empty: TemporaryBurstData = {
    active: false,
    session_id: null,
    started_at: null,
    expires_at: null,
    ended_at: null,
    auto_apply: state.settings.auto_apply_recommendations,
    monitoring_enabled: state.settings.monitoring_enabled,
    recommended_balance_usd: 9999,
    can_start: true,
    cycles: [],
  };
  if (method === "GET") return ok(state.temporaryBurst ?? empty);
  if (method !== "POST") return fail("不支持此操作", 405);
  if (payload.confirm !== true) return fail("请确认开启临时爽蹬", 400);
  if (state.temporaryBurst && !state.temporaryBurst.can_start)
    return fail("本轮尚未结束结算", 409);
  const latest = state.observations.at(-1)!;
  const reset =
    Date.parse(latest.upstream_resets_at) > Date.parse(state.clock)
      ? latest.upstream_resets_at
      : new Date(Date.parse(state.clock) + 7 * 86400000).toISOString();
  const cycles: BurstCycle[] = state.monitoredAccounts
    .filter((account) => account.enabled && account.provider === "sub2api")
    .map((account) => ({
      account_id: account.id,
      account_name: account.name,
      resets_at: reset,
      is_burst_cycle: true,
      settled_at: null,
      evidence_at: null,
      error: "",
      settlement: [],
      members: state.participants
        .filter((person) => person.enabled)
        .flatMap((person) => {
          const allocation = person.account_breakdowns.find(
            (item) => item.account_id === account.id && item.allocated,
          );
          return allocation
            ? [
                {
                  participant_id: person.id,
                  user_id: person.sub2api_user_id,
                  name: person.name,
                  base_share: String(allocation.contract_share_percent),
                  opening_adjustment:
                    state.temporaryBurst?.cycles
                      .find(
                        (row) =>
                          row.account_id === account.id && !row.settled_at,
                      )
                      ?.members.find((row) => row.participant_id === person.id)
                      ?.opening_adjustment ?? "0",
                },
              ]
            : [];
        }),
    }));
  state.temporaryBurst = {
    ...empty,
    session_id: (state.temporaryBurst?.session_id ?? 0) + 1,
    active: true,
    can_start: false,
    started_at: state.clock,
    expires_at: reset,
    cycles,
  };
  refreshDemoBurst(state, true);
  return ok({
    ...state.temporaryBurst,
    application: {
      applied: state.settings.auto_apply_recommendations
        ? state.participants.filter((p) => p.enabled).length
        : 0,
      failed: 0,
    },
  });
}
