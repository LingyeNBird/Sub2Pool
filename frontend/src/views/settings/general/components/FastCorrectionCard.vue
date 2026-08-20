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
        <AppIcon name="bolt" class="size-5" />兼容旧版 FAST 修正
      </h2>

      <div class="flex items-center justify-between gap-4">
        <SettingLabel
          label="补足 Sub2API 的 2 倍 FAST 计费"
          help="仅当 Sub2API 实际仍按 2 倍记录 FAST 成本时开启。Sub2API 0.1.179 起可在渠道定价中直接配置 FAST 倍率；渠道已设为 2.5 时必须关闭此处，避免重复修正。关闭只停止新事实采集，历史修正永久保留。"
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
          事实。旧记录无法证明完整请求日志时会保持未知，不会根据当前可查询数据推测或覆盖。
        </span>
      </div>

      <p class="text-sm leading-6 opacity-70">
        推荐在 Sub2API 的 OpenAI OAuth 渠道中把 FAST 倍率设置为
        2.5，并保持此兼容开关关闭。旧版或仍按 2 倍计费的渠道可以重新开启。
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
