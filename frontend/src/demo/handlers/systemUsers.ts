import type { SystemUser } from "@/types/security";
import {
  accountScopedPagePermissions,
  pagePermissionCodes,
  participantScopedPagePermissions,
  type PagePermission,
} from "@/config/pagePermissions";

import type { DemoRequestContext } from "../backend";
import { saveDemoState, type DemoState } from "../state";
import { participantNames } from "./participants";

export function accountNames(state: DemoState, ids: number[]): string[] {
  return ids.flatMap((id) => {
    const account = state.monitoredAccounts.find((item) => item.id === id);
    return account ? [account.name] : [];
  });
}

export function handleSystemUsers({
  method,
  pathname,
  payload,
  state,
  ok,
  fail,
}: DemoRequestContext): Response | null {
  if (method === "GET" && pathname === "system-users") {
    return ok(state.systemUsers);
  }
  if (method === "POST" && pathname === "system-users") {
    const username = String(payload.username ?? "").trim();
    if (state.systemUsers.some((item) => item.username === username)) {
      return fail("用户字段格式无效", 400, { username: ["用户名已存在"] });
    }
    const participantIds: number[] = [];
    const item: SystemUser = {
      id: state.nextSystemUserId++,
      username,
      email: String(payload.email ?? ""),
      is_active: payload.is_active !== false,
      page_permissions: [],
      participant_ids: participantIds,
      participant_names: participantNames(state, participantIds),
      account_ids: [],
      account_names: [],
      last_login: null,
      date_joined: state.clock,
    };
    state.systemUsers.push(item);
    saveDemoState(state);
    return ok(item, 201);
  }
  const systemUserMatch = /^system-users\/(\d+)$/.exec(pathname);
  if (systemUserMatch && method === "PATCH") {
    const item = state.systemUsers.find(
      (user) => user.id === Number(systemUserMatch[1]),
    );
    if (!item) return fail("系统用户不存在", 404);
    if (typeof payload.username === "string") {
      item.username = payload.username.trim();
    }
    if (typeof payload.email === "string") item.email = payload.email;
    if (typeof payload.is_active === "boolean") {
      item.is_active = payload.is_active;
    }
    saveDemoState(state);
    return ok(item);
  }
  const systemUserPermissionMatch = /^system-users\/(\d+)\/permissions$/.exec(
    pathname,
  );
  if (systemUserPermissionMatch && method === "PATCH") {
    const item = state.systemUsers.find(
      (user) => user.id === Number(systemUserPermissionMatch[1]),
    );
    if (!item) return fail("系统用户不存在", 404);
    const requestedPages = Array.isArray(payload.page_permissions)
      ? payload.page_permissions.map(String)
      : [];
    const validPages = new Set<string>(pagePermissionCodes);
    if (requestedPages.some((page) => !validPages.has(page))) {
      return fail("系统用户权限校验失败", 400, {
        page_permissions: ["包含未知页面权限"],
      });
    }
    if (new Set(requestedPages).size !== requestedPages.length) {
      return fail("系统用户权限校验失败", 400, {
        page_permissions: ["页面权限不能重复"],
      });
    }
    const participantIds = Array.isArray(payload.participant_ids)
      ? payload.participant_ids.map(Number)
      : [];
    if (
      participantIds.some(
        (id) =>
          !state.participants.some((participant) => participant.id === id),
      )
    ) {
      return fail("系统用户权限校验失败", 400, {
        participant_ids: ["包含不存在的参与者"],
      });
    }
    const accountIds = Array.isArray(payload.account_ids)
      ? payload.account_ids.map(Number)
      : [];
    if (
      accountIds.some(
        (id) => !state.monitoredAccounts.some((account) => account.id === id),
      )
    ) {
      return fail("系统用户权限校验失败", 400, {
        account_ids: ["包含不存在的账号"],
      });
    }
    if (
      requestedPages.some((page) =>
        participantScopedPagePermissions.has(page as PagePermission),
      ) &&
      !participantIds.length
    ) {
      return fail("系统用户权限校验失败", 400, {
        participant_ids: [
          "已开放包含参与者数据的页面，请至少选择一个可查看的参与者",
        ],
      });
    }
    if (
      requestedPages.some((page) =>
        accountScopedPagePermissions.has(page as PagePermission),
      ) &&
      !accountIds.length
    ) {
      return fail("系统用户权限校验失败", 400, {
        account_ids: ["已开放包含账号数据的页面，请至少选择一个可查看的账号"],
      });
    }
    item.page_permissions = requestedPages as PagePermission[];
    item.participant_ids = participantIds;
    item.participant_names = participantNames(state, participantIds);
    item.account_ids = accountIds;
    item.account_names = accountNames(state, accountIds);
    saveDemoState(state);
    return ok(item);
  }
  if (systemUserMatch && method === "DELETE") {
    state.systemUsers = state.systemUsers.filter(
      (item) => item.id !== Number(systemUserMatch[1]),
    );
    saveDemoState(state);
    return ok();
  }
  return null;
}
