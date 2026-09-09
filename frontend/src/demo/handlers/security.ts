import type {
  AnnouncementListData,
  AnnouncementRecord,
  BlockedIPAddress,
  LoginEventData,
} from "@/types/security";

import type { DemoRequestContext } from "../backend";
import { demoIdentity, saveDemoState, type DemoState } from "../state";

const DEMO_ANNOUNCEMENTS: Array<Omit<AnnouncementRecord, "read" | "read_at">> =
  [
    {
      code: "sub2api-upstream-pricing-2026-09-08",
      title: "计费倍率改由 Sub2API 统一配置",
      published_at: "2026-09-08T00:00:00Z",
      severity: "info",
      paragraphs: [
        "演示模式使用合成上游计费状态，不发送真实管理请求。请选择目标分组并确认应用，未选择时不会自动写入。",
        "旧记录使用冻结的本地规则，新记录不再叠加本地修正。撤回恢复接管前配置，不会自动重新应用。",
      ],
    },
    {
      code: "auto-apply-recommendations-default-2026-09-08",
      title: "自动应用建议额度默认开启",
      published_at: "2026-09-08T00:00:00Z",
      severity: "info",
      paragraphs: [
        "因为现在额度测算较为完善，所以默认为您打开“自动应用建议额度”。如不需要，请在系统设置里面关闭。",
      ],
    },
  ];

function announcementData(state: DemoState): AnnouncementListData {
  const reads = new Set(state.announcementReads ?? []);
  const items = DEMO_ANNOUNCEMENTS.map((item) => ({
    ...item,
    ...(item.code === "sub2api-upstream-pricing-2026-09-08"
      ? {
          pricing_status: state.upstreamPricing.status,
          can_revert: state.upstreamPricing.can_revert,
          pricing_revision: state.upstreamPricing.revision,
          can_apply_pricing: !state.upstreamPricing.announcement_applied_at,
          paragraphs: [
            `合成操作状态：${state.upstreamPricing.status === "reverted" ? "已撤回" : "已应用"}`,
            ...item.paragraphs,
          ],
        }
      : {
          auto_apply_recommendations: state.settings.auto_apply_recommendations,
        }),
    read: reads.has(item.code),
    read_at: reads.has(item.code) ? state.clock : null,
  }));
  return {
    items,
    unread_count: items.filter((item) => !item.read).length,
  };
}

function loginEventsData(context: DemoRequestContext): LoginEventData {
  const { state, paginate } = context;
  const page = paginate(state.loginEvents);
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

export function handleSecurity(context: DemoRequestContext): Response | null {
  const { method, pathname, payload, state, ok, fail } = context;
  if (pathname === "announcements" && method === "GET") {
    if (!demoIdentity()?.is_staff) return fail("没有管理员权限", 403);
    return ok(announcementData(state));
  }
  const announcementReadMatch = /^announcements\/([a-z0-9-]+)\/read$/.exec(
    pathname,
  );
  if (announcementReadMatch && method === "POST") {
    if (!demoIdentity()?.is_staff) return fail("没有管理员权限", 403);
    const item = DEMO_ANNOUNCEMENTS.find(
      (candidate) => candidate.code === announcementReadMatch[1],
    );
    if (!item) return fail("公告不存在", 404);
    state.announcementReads ??= [];
    if (!state.announcementReads.includes(item.code)) {
      state.announcementReads.push(item.code);
      saveDemoState(state);
    }
    return ok(
      announcementData(state).items.find((record) => record.code === item.code),
    );
  }
  if (method === "GET" && pathname === "login-events") {
    return ok(loginEventsData(context));
  }
  if (method === "GET" && pathname === "ip-blocks") {
    return ok(state.blockedAddresses);
  }
  if (method === "POST" && pathname === "ip-blocks") {
    if (
      state.blockedAddresses.some((item) => item.address === payload.address)
    ) {
      return fail("该地址已经封禁", 400);
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
    return ok(item, 201);
  }
  const ipBlockMatch = /^ip-blocks\/(\d+)$/.exec(pathname);
  if (method === "DELETE" && ipBlockMatch) {
    state.blockedAddresses = state.blockedAddresses.filter(
      (item) => item.id !== Number(ipBlockMatch[1]),
    );
    saveDemoState(state);
    return ok();
  }
  return null;
}
