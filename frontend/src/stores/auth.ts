import { ref } from "vue";
import { defineStore } from "pinia";

import {
  api,
  clearAccessToken,
  jsonBody,
  setAccessToken,
} from "@/services/api";
import type { WebRtcNetworkInfo } from "@/services/webrtc";

export const useAuthStore = defineStore("auth", () => {
  const username = ref("");
  const isStaff = ref(false);
  const ready = ref(false);

  function expire(): void {
    clearAccessToken();
    username.value = "";
    isStaff.value = false;
    ready.value = true;
  }

  async function refresh(): Promise<boolean> {
    try {
      // 页面刷新后 Access Token 已从内存消失；请求覆写会先用 HttpOnly
      // Refresh Cookie 换取新 Access Token，再自动重放本次 /auth/me。
      const data = await api<{ username: string; is_staff: boolean }>(
        "auth/me",
      );
      username.value = data.username;
      isStaff.value = data.is_staff;
      ready.value = true;
      return true;
    } catch {
      expire();
      return false;
    }
  }

  async function signIn(
    loginUsername: string,
    password: string,
    clientNetwork: WebRtcNetworkInfo,
  ): Promise<void> {
    const data = await api<{
      username: string;
      is_staff: boolean;
      access: string;
    }>("auth/login", {
      method: "POST",
      body: jsonBody({
        username: loginUsername,
        password,
        client_network: clientNetwork,
      }),
    });
    setAccessToken(data.access);
    username.value = data.username;
    isStaff.value = data.is_staff;
    ready.value = true;
  }

  async function signOut(): Promise<void> {
    try {
      await api("auth/logout", { method: "POST" });
    } finally {
      expire();
    }
  }

  return {
    username,
    isStaff,
    ready,
    refresh,
    signIn,
    signOut,
    expire,
  };
});
