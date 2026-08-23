import { pagePermissionCodes } from "@/config/pagePermissions";

import type { DemoRequestContext } from "../backend";
import { clearDemoIdentity, demoIdentity, setDemoIdentity } from "../state";

export function handlePublicAuth({
  method,
  pathname,
  payload,
  ok,
  fail,
}: DemoRequestContext): Response | null {
  if (method === "GET" && pathname === "auth/client-config") {
    return ok({ webrtc_enabled: false, stun_url: "" });
  }
  if (method === "POST" && pathname === "auth/network-check") {
    return ok({ allowed: true });
  }
  if (method === "POST" && pathname === "auth/login") {
    if (payload.username !== "admin" || payload.password !== "123456") {
      return fail("用户名或密码错误", 401);
    }
    const identity = {
      username: "admin",
      is_staff: true,
      page_permissions: [...pagePermissionCodes],
      timezone: "Asia/Shanghai",
    };
    setDemoIdentity(identity);
    return ok({ ...identity, access: "demo_access_public_pages" });
  }
  if (
    (method === "GET" && pathname === "auth/me") ||
    (method === "POST" && pathname === "auth/refresh")
  ) {
    const identity = demoIdentity();
    return identity
      ? ok(
          method === "POST" ? { access: "demo_access_public_pages" } : identity,
        )
      : fail("登录已过期，请重新登录", 401);
  }
  if (method === "POST" && pathname === "auth/logout") {
    clearDemoIdentity();
    return ok();
  }
  return null;
}

export function handleProtectedAuth({
  method,
  pathname,
  ok,
}: DemoRequestContext): Response | null {
  if (method === "POST" && pathname === "auth/password") {
    return ok({ changed: true, access: "demo_access_public_pages" });
  }
  return null;
}
