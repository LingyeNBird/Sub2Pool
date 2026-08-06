import { ref } from "vue";
import { defineStore } from "pinia";

import { api, jsonBody } from "@/services/api";
import type { WebRtcNetworkInfo } from "@/services/webrtc";

export const useAuthStore = defineStore("auth", () => {
  const username = ref("");
  const ready = ref(false);

  async function refresh(): Promise<boolean> {
    try {
      const data = await api<{ username: string }>("auth/me");
      username.value = data.username;
      ready.value = true;
      return true;
    } catch {
      username.value = "";
      ready.value = true;
      return false;
    }
  }

  async function signIn(
    loginUsername: string,
    password: string,
    clientNetwork: WebRtcNetworkInfo,
  ): Promise<void> {
    const data = await api<{ username: string }>("auth/login", {
      method: "POST",
      body: jsonBody({
        username: loginUsername,
        password,
        client_network: clientNetwork,
      }),
    });
    username.value = data.username;
    ready.value = true;
  }

  async function signOut(): Promise<void> {
    await api("auth/logout", { method: "POST" });
    username.value = "";
  }

  return { username, ready, refresh, signIn, signOut };
});
