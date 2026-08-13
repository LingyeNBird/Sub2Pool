<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { ApiError, api, apiRaw } from "@/services/api";
import {
  collectWebRtcNetworkInfo,
  type WebRtcNetworkInfo,
} from "@/services/webrtc";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const username = ref("");
const password = ref("");
const loading = ref(false);
const networkReady = ref(false);
const networkBlocked = ref(false);
const message = ref("");
const demoMode = import.meta.env.VITE_DEMO_MODE === "true";
const faviconUrl = `${import.meta.env.BASE_URL}favicon.png`;
const demoCredentials = computed(() =>
  demoMode ? "账号 admin，密码 123456" : "",
);
const clientNetwork = ref<WebRtcNetworkInfo>({
  webrtc_supported: false,
  webrtc_ips: [],
});

async function checkLoginNetwork() {
  try {
    const config = await api<{
      webrtc_enabled: boolean;
      stun_url: string;
    }>("auth/client-config");
    clientNetwork.value = demoMode
      ? { webrtc_supported: false, webrtc_ips: [] }
      : await collectWebRtcNetworkInfo(config.webrtc_enabled, config.stun_url);
    const response = await apiRaw("auth/network-check", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ client_network: clientNetwork.value }),
    });
    if (response.status === 204) {
      networkBlocked.value = true;
      return;
    }
  } catch {
    // WebRTC 是浏览器自报的辅助线索。预检异常时仍允许管理员通过服务端 IP 鉴权。
  }
  networkReady.value = true;
}

async function submit() {
  loading.value = true;
  message.value = "";
  try {
    await auth.signIn(username.value, password.value, clientNetwork.value);
    const next =
      typeof route.query.next === "string" && route.query.next.startsWith("/")
        ? route.query.next
        : auth.isStaff
          ? "/"
          : "/statistics";
    await router.replace(next);
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "登录失败";
  } finally {
    loading.value = false;
  }
}

onMounted(checkLoginNetwork);
</script>

<template>
  <main class="hero min-h-screen bg-base-200">
    <div
      v-if="networkReady && !networkBlocked"
      class="hero-content w-full max-w-md"
    >
      <section class="card w-full bg-base-100 shadow-xs">
        <div class="card-body gap-5">
          <div>
            <div class="mb-3 flex items-center gap-2 font-semibold">
              <img :src="faviconUrl" alt="" class="size-5 rounded-md" />
              Sub2API 拼车额度
            </div>
            <h1 class="card-title text-2xl">用户登录</h1>
            <div v-if="demoMode" class="mt-4 alert text-sm alert-info">
              <AppIcon name="information-circle" class="size-5" />
              <span>
                公开演示仅使用合成数据，不连接真实服务。{{ demoCredentials }}
              </span>
            </div>
          </div>
          <div v-if="message" class="alert alert-soft text-sm alert-error">
            <AppIcon name="exclamation-triangle" class="size-5" />
            <span>{{ message }}</span>
          </div>
          <form class="grid gap-4" @submit.prevent="submit">
            <fieldset class="fieldset">
              <label class="label" for="username">用户名</label>
              <input
                id="username"
                v-model="username"
                class="input w-full"
                autocomplete="username"
                required
                autofocus
              />
            </fieldset>
            <fieldset class="fieldset">
              <label class="label" for="password">密码</label>
              <input
                id="password"
                v-model="password"
                type="password"
                class="input w-full"
                autocomplete="current-password"
                required
              />
            </fieldset>
            <button class="btn w-full btn-primary" :disabled="loading">
              <span
                v-if="loading"
                class="loading loading-sm loading-spinner"
              ></span>
              {{ loading ? "正在登录" : "登录" }}
            </button>
          </form>
        </div>
      </section>
    </div>
  </main>
</template>
