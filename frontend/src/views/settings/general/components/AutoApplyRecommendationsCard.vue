<script setup lang="ts">
import type { AppSettingsData } from "@/types/settings";

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
        <AppIcon name="sparkles" class="size-5" />自动应用建议额度
      </h2>
      <p
        id="auto-apply-recommendations-description"
        class="text-sm leading-6 opacity-70"
      >
        启用后，后台复用已保存的采样间隔；每次定时采样完成后，仅在存在当前有效建议时应用。没有有效建议不会更新余额；暂停后台监控时不会执行自动应用。
      </p>
      <label class="label justify-between gap-4">
        启用自动应用
        <input
          v-model="settings.auto_apply_recommendations"
          type="checkbox"
          class="toggle toggle-sm"
          aria-describedby="auto-apply-recommendations-description"
        />
      </label>
      <button
        class="btn btn-primary btn-sm"
        :disabled="saving"
        @click="emit('save')"
      >
        <span v-if="saving" class="loading loading-xs loading-spinner"></span>
        <AppIcon v-else name="check" class="size-4" />保存自动应用设置
      </button>
    </div>
  </section>
</template>
