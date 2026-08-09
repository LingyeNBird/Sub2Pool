<script setup lang="ts">
import SettingLabel from "@/components/common/SettingLabel.vue";
import type { AppSettingsData } from "@/types";

const settings = defineModel<AppSettingsData>("settings", { required: true });
defineProps<{ saving: boolean; rebuilding: boolean }>();
const emit = defineEmits<{ save: []; rebuild: [] }>();
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body">
      <h2 class="card-title">
        <AppIcon name="bolt" class="size-5" />FAST 修正
      </h2>

      <div class="flex items-center justify-between gap-4">
        <SettingLabel
          label="计算 FAST 修正"
          help="开启后，每次成功形成上游观测时都会只读该采样区间内的 Sub2API 请求日志，把 FAST 请求从 Sub2API 当前的 2 倍口径修正为上游套餐的 2.5 倍口径。关闭后停止计算新采样，已有修正事实不会删除。"
        />
        <input
          v-model="settings.fast_correction_enabled"
          type="checkbox"
          class="toggle shrink-0 toggle-sm"
        />
      </div>

      <div
        v-if="settings.fast_correction_rebuild_recommended"
        class="alert text-sm alert-warning"
      >
        <AppIcon name="exclamation-triangle" class="size-5 shrink-0" />
        <span>
          当前周期有
          {{ settings.fast_correction_missing_intervals }}
          个采样区间尚未计算 FAST 修正，建议执行修正重建。
        </span>
      </div>

      <p class="text-sm leading-6 opacity-70">
        仅额外保存修正值，不修改 Sub2API
        原始用量。重建会重新读取请求日志，并重放受影响的额度估算与归属结论。
      </p>

      <div class="grid gap-2 sm:grid-cols-2">
        <button
          type="button"
          class="btn btn-primary btn-sm"
          :disabled="saving || rebuilding"
          @click="emit('save')"
        >
          <span v-if="saving" class="loading loading-xs loading-spinner"></span>
          <AppIcon v-else name="check" class="size-4" />保存 FAST 设置
        </button>
        <button
          type="button"
          class="btn btn-sm"
          :disabled="saving || rebuilding"
          @click="emit('rebuild')"
        >
          <span
            v-if="rebuilding"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="arrow-path" class="size-4" />修正重建
        </button>
      </div>
    </div>
  </section>
</template>
