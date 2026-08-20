<script setup lang="ts">
import { onMounted, ref } from "vue";

import { api, jsonBody } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { AppSettingsData } from "@/types";

const auth = useAuthStore();
const visible = ref(false);
const dismissing = ref(false);
const error = ref("");

onMounted(async () => {
  if (!auth.isStaff) return;
  try {
    const settings = await api<AppSettingsData>("settings");
    visible.value = settings.fast_pricing_upgrade_notice_pending;
  } catch {
    // 其他页面请求会显示连接或登录错误；公告读取失败不应阻断页面。
  }
});

async function dismiss() {
  dismissing.value = true;
  error.value = "";
  try {
    const settings = await api<AppSettingsData>("settings", {
      method: "PATCH",
      body: jsonBody({ fast_pricing_upgrade_notice_pending: false }),
    });
    visible.value = settings.fast_pricing_upgrade_notice_pending;
  } catch {
    error.value = "公告状态保存失败，请稍后重试。";
  } finally {
    dismissing.value = false;
  }
}
</script>

<template>
  <section
    v-if="visible"
    class="mx-4 mt-4 rounded-box border border-warning/35 bg-warning/10 px-4 py-3 text-sm lg:mx-10 lg:mt-6"
    aria-live="polite"
  >
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="flex max-w-4xl min-w-0 items-start gap-3">
        <AppIcon
          name="exclamation-triangle"
          class="mt-0.5 size-5 shrink-0 text-warning"
        />
        <div>
          <h2 class="font-semibold">请调整 Sub2API 的 FAST 倍率</h2>
          <p class="mt-1 leading-6 opacity-75">
            Sub2API 0.1.179 起可以在渠道定价中直接设置倍率。Sub2Pool
            已关闭后续兼容修正并保留历史修正；请把 OpenAI OAuth 渠道的 FAST
            倍率设置为 2.5。若渠道仍按 2
            倍计费，请在系统设置中重新开启兼容修正。
          </p>
        </div>
      </div>
      <div class="flex shrink-0 flex-wrap gap-2">
        <RouterLink to="/settings" class="btn btn-outline btn-sm">
          检查 FAST 设置
        </RouterLink>
        <button
          type="button"
          class="btn btn-ghost btn-sm"
          :disabled="dismissing"
          @click="dismiss"
        >
          <span
            v-if="dismissing"
            class="loading loading-xs loading-spinner"
          ></span>
          我已了解
        </button>
      </div>
    </div>
    <p v-if="error" class="mt-2 text-error">{{ error }}</p>
  </section>
</template>
