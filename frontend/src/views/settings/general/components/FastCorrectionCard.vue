<script setup lang="ts">
import SettingLabel from "@/components/common/SettingLabel.vue";
import type { AppSettingsData } from "@/types";

const settings = defineModel<AppSettingsData>("settings", { required: true });
defineProps<{ saving: boolean }>();
const emit = defineEmits<{ save: [] }>();
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
        class="alert items-start text-sm alert-warning"
      >
        <AppIcon name="exclamation-triangle" class="mt-0.5 size-5 shrink-0" />
        <span>
          当前周期有
          {{ settings.fast_correction_missing_intervals }}
          个采样区间缺少 FAST
          事实。历史补齐只能在下方“数据维护”中创建“远端验证修复”计划；coverage
          未验证时只显示阻断，不会覆盖现有历史。
        </span>
      </div>

      <p class="text-sm leading-6 opacity-70">
        开启后只影响后续完整采样。历史 FAST
        事实与账号、逐用户成本作为同一事实组应用，不再提供独立重建或强制覆盖入口。
      </p>

      <button
        type="button"
        class="btn btn-primary btn-sm"
        :disabled="saving"
        @click="emit('save')"
      >
        <span v-if="saving" class="loading loading-xs loading-spinner"></span>
        <AppIcon v-else name="check" class="size-4" />保存 FAST 设置
      </button>
    </div>
  </section>
</template>
