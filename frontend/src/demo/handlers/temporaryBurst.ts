import type { DemoRequestContext } from "../backend";
import type { DemoState } from "../state";
import { aggregateParticipant, demoIdentity, saveDemoState } from "../state";
import type {
  BurstCycle,
  BurstMember,
  TemporaryBurstData,
} from "@/types/temporaryBurst";
import type { CarryAdjustment } from "@/types/participants";

export function demoMemberAdjustment(cycle: BurstCycle, member: BurstMember) {
  for (let index = (cycle.carry_edits?.length ?? 0) - 1; index >= 0; index--) {
    const edit = cycle.carry_edits![index]!;
    if (
      edit.participant_id === member.participant_id &&
      edit.user_id === member.user_id
    )
      return Number(edit.after);
  }
  return Number(member.opening_adjustment);
}

export function demoCarryRows(state: DemoState): CarryAdjustment[] {
  return (state.temporaryBurst?.cycles ?? []).flatMap((cycle) => {
    if (
      cycle.settled_at ||
      Date.parse(cycle.resets_at) <= Date.parse(state.clock)
    )
      return [];
    const account = state.monitoredAccounts.find(
      (row) => row.id === cycle.account_id,
    );
    const pool = state.quotaPools.find((row) => row.id === account?.pool_id);
    return cycle.members.flatMap((member) => {
      const person = state.participants.find(
        (row) => row.id === member.participant_id,
      );
      const value = demoMemberAdjustment(cycle, member);
      if (
        !person?.enabled ||
        person.sub2api_user_id !== member.user_id ||
        !value ||
        !pool?.allocations.some(
          (row) => row.participant_id === person.id && row.share_percent > 0,
        )
      )
        return [];
      return [
        {
          cycle_id: cycle.cycle_id,
          account_id: cycle.account_id,
          participant_id: member.participant_id,
          user_id: member.user_id,
          resets_at: cycle.resets_at,
          adjustment_percent: String(value),
          revision: cycle.carry_edits?.length ?? 0,
        },
      ];
    });
  });
}

function reminderEmailReady(state: DemoState) {
  const settings = state.settings;
  return Boolean(
    settings.notification_email.trim() &&
    (settings.email_provider === "resend"
      ? settings.resend_from_email.trim() && settings.resend_api_key_configured
      : settings.smtp_host.trim() &&
        settings.smtp_from_email.trim() &&
        (!settings.smtp_username.trim() || settings.smtp_password_configured)),
  );
}

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
      (member) =>
        Number(member.base_share) + demoMemberAdjustment(cycle, member),
    );
    const unused = rights.map((right, index) =>
      Math.max(0, right - usage[index]!),
    );
    const excess = rights.map((right, index) =>
      Math.max(0, usage[index]! - right),
    );
    const spare = unused.reduce((a, b) => a + b, 0);
    const over = excess.reduce((a, b) => a + b, 0);
    const finalObservation = state.observations
      .filter(
        (row) =>
          row.account_id === cycle.account_id &&
          row.upstream_resets_at === cycle.resets_at,
      )
      .at(-1);
    const remaining = Math.max(
      0,
      100 - (finalObservation?.upstream_used_percent ?? 0),
    );
    const total = remaining < 5 ? Math.min(spare, over) : 0;
    cycle.settlement_context = {
      remaining_percent: String(remaining),
      threshold_percent: "5",
      eligible: remaining < 5,
      quota_observed_at: finalObservation?.observed_at ?? cycle.resets_at,
      seconds_before_reset: finalObservation
        ? Math.max(
            0,
            (Date.parse(cycle.resets_at) -
              Date.parse(finalObservation.observed_at)) /
              1000,
          )
        : 0,
      reason:
        remaining < 5
          ? "上周期剩余小于 5%，按借用情况结算"
          : "上周期剩余大于或等于 5%，本轮调整为 0",
    };
    cycle.settlement = cycle.members.map((member, index) => ({
      ...member,
      opening_adjustment: String(demoMemberAdjustment(cycle, member)),
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
        cycle_id: Math.max(0, ...mode.cycles.map((row) => row.cycle_id)) + 1,
        carry_edits: [],
        is_burst_cycle: false,
        resets_at: new Date(
          Date.parse(cycle.resets_at) + 7 * 86400000,
        ).toISOString(),
        settled_at: null,
        evidence_at: null,
        settlement: [],
        settlement_context: undefined,
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
  mode.enabled_account_count = state.monitoredAccounts.filter(
    (row) => row.enabled && row.provider === "sub2api",
  ).length;
  mode.sampling = state.monitoredAccounts
    .filter((row) => row.enabled && row.provider === "sub2api")
    .map((account) => {
      const cycle = mode.cycles.find(
        (row) =>
          row.account_id === account.id &&
          row.is_burst_cycle &&
          !row.settled_at,
      );
      const latest = state.observations
        .filter((row) => row.account_id === account.id)
        .at(-1);
      const accelerated = Boolean(cycle && mode.monitoring_enabled);
      const nearEnd = cycle && Date.parse(cycle.resets_at) - now <= 30 * 60000;
      const normal = Math.max(2, state.settings.local_poll_minutes) * 60;
      return {
        account_id: account.id,
        account_name: account.name,
        accelerated,
        interval_seconds: accelerated
          ? nearEnd || (latest?.upstream_used_percent ?? 0) >= 90
            ? 60
            : normal / 2
          : normal,
      };
    });
  mode.can_start =
    !mode.active &&
    !mode.cycles.some((cycle) => cycle.is_burst_cycle && !cycle.settled_at);
  mode.reminder_email_ready = reminderEmailReady(state);
  if (mode.can_start) mode.reminder_enabled = false;
  if (
    mode.reminder_enabled &&
    mode.reminder_email_ready &&
    mode.monitoring_enabled
  ) {
    for (const cycle of mode.cycles.filter(
      (row) =>
        row.is_burst_cycle &&
        !row.settled_at &&
        now < Date.parse(row.resets_at),
    )) {
      const observation = state.observations
        .filter(
          (row) =>
            row.account_id === cycle.account_id &&
            row.upstream_resets_at === cycle.resets_at,
        )
        .at(-1);
      if (!observation || observation.upstream_used_percent < 95) continue;
      const subject = `[演示] 爽蹬用满提醒：${cycle.account_name} #${cycle.account_id} · ${cycle.resets_at}`;
      if (
        state.notifications.some(
          (row) =>
            row.subject === subject &&
            now - Date.parse(row.created_at) < 30 * 60000,
        )
      )
        continue;
      state.notifications.unshift({
        id: Math.max(0, ...state.notifications.map((row) => row.id)) + 1,
        event_type: "temporary_burst_exhaustion",
        event_type_label: "爽蹬用满提醒",
        severity: "warning",
        participant_name: null,
        recipient: state.settings.notification_email,
        subject,
        body: `合成账号已用 ${observation.upstream_used_percent}%。本功能不会自动使用重置卡；用卡前请告知所有车友。`,
        status: "skipped",
        status_label: "已跳过",
        error: "公开演示仅记录模拟提醒，不发送真实邮件",
        created_at: state.clock,
        sent_at: null,
      });
    }
  }
  for (const person of state.participants) {
    const adjustments: Record<number, number> = {};
    for (const cycle of mode.cycles.filter((row) => !row.settled_at)) {
      const member = cycle.members.find(
        (row) =>
          row.participant_id === person.id &&
          row.user_id === person.sub2api_user_id,
      );
      if (member)
        adjustments[cycle.account_id] = demoMemberAdjustment(cycle, member);
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
    enabled_account_count: state.monitoredAccounts.filter(
      (row) => row.enabled && row.provider === "sub2api",
    ).length,
    sampling: [],
    reminder_enabled: false,
    reminder_email_ready: reminderEmailReady(state),
  };
  if (method === "GET") return ok(state.temporaryBurst ?? empty);
  if (method === "PATCH") {
    const mode = state.temporaryBurst;
    if (!mode || payload.session_id !== mode.session_id)
      return fail("本轮状态已变化，请刷新", 400);
    if (typeof payload.reminder_enabled !== "boolean")
      return fail("请指定提醒开关", 400);
    if (
      payload.reminder_enabled &&
      (!mode.reminder_email_ready || mode.can_start)
    )
      return fail("请先配置邮件通知并开启本轮爽蹬", 400);
    mode.reminder_enabled = payload.reminder_enabled;
    saveDemoState(state);
    return ok(mode);
  }
  if (method !== "POST") return fail("不支持此操作", 405);
  if (payload.confirm !== true) return fail("请确认开启临时爽蹬", 400);
  if (payload.riders_notified !== true)
    return fail("请先告知所有车友重置卡与共享余额的影响", 400);
  if (state.temporaryBurst && !state.temporaryBurst.can_start)
    return fail("本轮尚未结束结算", 409);
  const latest = state.observations.at(-1)!;
  const reset =
    Date.parse(latest.upstream_resets_at) > Date.parse(state.clock)
      ? latest.upstream_resets_at
      : new Date(Date.parse(state.clock) + 7 * 86400000).toISOString();
  const cycles: BurstCycle[] = state.monitoredAccounts
    .filter((account) => account.enabled && account.provider === "sub2api")
    .map((account, index) => ({
      cycle_id:
        Math.max(
          0,
          ...(state.temporaryBurst?.cycles ?? []).map((row) => row.cycle_id),
        ) +
        index +
        1,
      carry_edits: [],
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
                    demoCarryRows(state).find(
                      (row) =>
                        row.account_id === account.id &&
                        row.participant_id === person.id,
                    )?.adjustment_percent ?? "0",
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
