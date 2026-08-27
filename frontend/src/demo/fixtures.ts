import type { MonitoredAccount } from "@/types/accounts";
import type { Participant, QuotaPoolAllocation } from "@/types/participants";
import type { LoginEventRecord, NotificationRecord } from "@/types/security";
import type { AppSettingsData } from "@/types/settings";

import type { DemoState } from "./state";
import { aggregateParticipant, snapshot } from "./participantProjection";
import {
  buildPeriods,
  DAY,
  DEMO_ANCHOR,
  HOUR,
  iso,
} from "./trajectoryFixtures";

function baseMonitoredAccounts(): MonitoredAccount[] {
  return [
    {
      id: 1,
      pool_id: 1,
      external_account_id: 8801,
      name: "主力账号",
      enabled: true,
      quota_query_mode: "passive",
      quota_profile: "auto",
      detected_plan_type: "pro",
      effective_quota_profile: "pro_20x",
      capacity_min_usd_override: null,
      capacity_max_usd_override: null,
      capacity_min_usd: 1400,
      capacity_max_usd: 4000,
      last_local_check_at: iso(DEMO_ANCHOR - 2 * 60_000),
      last_upstream_check_at: iso(DEMO_ANCHOR - 3 * 60_000),
      last_success_at: iso(DEMO_ANCHOR - 2 * 60_000),
      next_local_check_at: iso(DEMO_ANCHOR + 8 * 60_000),
      last_error: "",
    },
    {
      id: 2,
      pool_id: 1,
      external_account_id: 8802,
      name: "备用账号",
      enabled: true,
      quota_query_mode: "direct",
      quota_profile: "plus",
      detected_plan_type: "",
      effective_quota_profile: "plus",
      capacity_min_usd_override: null,
      capacity_max_usd_override: null,
      capacity_min_usd: 100,
      capacity_max_usd: 200,
      last_local_check_at: iso(DEMO_ANCHOR - 5 * 60_000),
      last_upstream_check_at: iso(DEMO_ANCHOR - 7 * 60_000),
      last_success_at: iso(DEMO_ANCHOR - 5 * 60_000),
      next_local_check_at: iso(DEMO_ANCHOR + 8 * 60_000),
      last_error: "",
    },
  ];
}

function baseParticipants(accounts: MonitoredAccount[]): Participant[] {
  const poolName = "默认混池";
  const rows = [
    {
      id: 1,
      name: "青岚",
      email: "owner@example.test",
      sub2api_user_id: 101,
      sub2api_username: "demo-owner",
      sub2api_email: "owner@example.test",
      sharePercent: 40,
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
      sharePercent: 35,
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
      sharePercent: 25,
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
    pool_allocations: [
      {
        pool_id: 1,
        pool_name: poolName,
        share_percent: row.sharePercent,
        account_ids: accounts.map((account) => account.id),
        account_count: accounts.length,
      },
    ],
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
      pool_id: 1,
      pool_name: poolName,
      contract_share_percent: row.sharePercent,
      allocated: true,
      latest_selected_cost: null,
      last_checked_at: null,
      snapshot: null,
    })),
    snapshot: null,
  }));
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
  const quotaPools: QuotaPoolAllocation[] = [
    {
      id: 1,
      name: "默认混池",
      contract_revision: 1,
      account_ids: monitoredAccounts.map((account) => account.id),
      allocations: participants.map((participant) => ({
        participant_id: participant.id,
        share_percent: participant.pool_allocations[0]?.share_percent ?? 0,
      })),
      total_share_percent: 100,
    },
  ];
  const { periods, observations } = buildPeriods(participants);
  const latest = observations[observations.length - 1];
  const latestSnapshots = latest.participants;
  for (const [index, participant] of participants.entries()) {
    const primaryBreakdown = participant.account_breakdowns[0];
    const secondaryBreakdown = participant.account_breakdowns[1];
    const primarySnapshot = latestSnapshots[index] ?? null;
    if (primarySnapshot) {
      primarySnapshot.source_sub2api_user_id = participant.sub2api_user_id;
    }
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
      const allocation = participant.pool_allocations.find(
        (item) => item.pool_id === secondaryBreakdown.pool_id,
      );
      const secondarySnapshot = snapshot(
        participant,
        allocation?.share_percent ?? 0,
        latest.selected_total_cost * 0.62,
        latest.interval_used_percent * 0.72,
        index,
        participant.latest_balance_usd ?? undefined,
      );
      secondarySnapshot.source_sub2api_user_id = participant.sub2api_user_id;
      secondaryBreakdown.snapshot = secondarySnapshot;
      secondaryBreakdown.latest_selected_cost = secondarySnapshot.selected_cost;
      secondaryBreakdown.last_checked_at = latest.observed_at;
    }
    aggregateParticipant(participant);
  }
  return {
    version: 14,
    clock: iso(DEMO_ANCHOR),
    nextParticipantId: 4,
    nextPoolId: 2,
    nextSystemUserId: 3,
    nextObservationId: observations.length + 1,
    nextBlockedId: 1,
    revision: 24,
    participants,
    monitoredAccounts,
    quotaPools,
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
        account_ids: [1],
        account_names: ["主力账号"],
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
        account_ids: [2],
        account_names: ["备用账号"],
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

export { initializeState };
