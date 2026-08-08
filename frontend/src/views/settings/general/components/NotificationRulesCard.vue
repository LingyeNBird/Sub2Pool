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
        <AppIcon name="bell-alert" class="size-5" />通知规则
      </h2>
      <label class="label justify-between"
        >参与者用户余额耗尽时通知<input
          v-model="settings.notify_on_limit_exhausted"
          type="checkbox"
          class="toggle toggle-sm"
      /></label>
      <label class="label justify-between"
        >建议金额明显变化时通知<input
          v-model="settings.notify_on_recommendation_change"
          type="checkbox"
          class="toggle toggle-sm"
      /></label>
      <label class="label justify-between"
        >美元 / 百分比明显变化时通知<input
          v-model="settings.notify_on_rate_change"
          type="checkbox"
          class="toggle toggle-sm"
      /></label>
      <label class="label justify-between"
        >采集失败时通知<input
          v-model="settings.notify_on_collection_error"
          type="checkbox"
          class="toggle toggle-sm"
      /></label>
      <div class="grid gap-3 md:grid-cols-2">
        <fieldset class="fieldset">
          <SettingLabel
            label="汇率变化阈值（%）"
            help="新旧保守美元/1% 估值的相对变化达到该百分比后，才发送“美元/百分比变化”通知。"
          />
          <input
            v-model.number="settings.rate_change_alert_percent"
            type="number"
            min="0"
            step="0.1"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset">
          <SettingLabel
            label="相同通知冷却（分钟）"
            help="相同去重键的通知在该时间内只发送一次，避免余额耗尽或采集失败反复发信。"
          />
          <input
            v-model.number="settings.notification_cooldown_minutes"
            type="number"
            min="1"
            class="input w-full"
          />
        </fieldset>
      </div>
      <button
        class="btn btn-primary btn-sm"
        :disabled="saving"
        @click="emit('save')"
      >
        <span v-if="saving" class="loading loading-xs loading-spinner"></span>
        <AppIcon v-else name="check" class="size-4" />保存通知规则
      </button>
    </div>
  </section>
</template>
