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
      code: "sub2api-fast-model-correction-0-1-179",
      title: "Sub2API 0.1.179 FAST 计费调整",
      published_at: "2026-08-20T00:00:00Z",
      severity: "warning",
      paragraphs: [
        "Sub2API 0.1.179 起支持在渠道模型定价规则中配置 FAST 倍率，但目前没有统一配置入口，需要针对各模型规则分别设置。建议优先在 Sub2API 中将需要的模型 FAST 倍率配置为 2.5。",
        "系统设置新增了更详细的 FAST 模型修正功能，支持模型通配符和从上到下的优先匹配。默认会把所有模型的 2 倍 FAST 成本修正为 2.5 倍。",
        "如果某个模型已在 Sub2API 中配置为 2.5 倍，可以在系统设置中为该模型添加 2.5 倍到 2.5 倍的规则，避免重复修正。历史 FAST 修正事实不受影响。",
      ],
    },
  ];

function announcementData(state: DemoState): AnnouncementListData {
  const reads = new Set(state.announcementReads ?? []);
  const items = DEMO_ANNOUNCEMENTS.map((item) => ({
    ...item,
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
    return ok({
      ...item,
      read: true,
      read_at: state.clock,
    } satisfies AnnouncementRecord);
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
