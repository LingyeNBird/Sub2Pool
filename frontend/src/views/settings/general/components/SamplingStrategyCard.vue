<script setup lang="ts">
import SettingLabel from "@/components/common/SettingLabel.vue";
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
        <AppIcon name="clock" class="size-5" />采样策略
      </h2>
      <div class="grid gap-3 md:grid-cols-2">
        <fieldset class="fieldset">
          <SettingLabel
            label="本地探测间隔（分钟）"
            help="后台按此间隔读取 Sub2API 本地用量和参与者余额。每次都会执行本地探测，但不一定读取周限快照或新增校准历史。"
          />
          <input
            v-model.number="settings.local_poll_minutes"
            type="number"
            min="2"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset">
          <SettingLabel
            label="进度触发阈值（%）"
            help="不是直接比较上游百分比。触发成本 = 当前保守美元/1% × 此百分比；本地成本增量达到触发成本后才读取新的周限快照。"
          />
          <input
            v-model.number="settings.progress_threshold_percent"
            type="number"
            min="0.1"
            max="10"
            step="0.1"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset">
          <SettingLabel
            label="活跃期最长校准间隔（小时）"
            help="已有本地成本增长但迟迟未达到进度阈值时，距离上次校准超过该时长会强制读取一次周限快照。"
          />
          <input
            v-model.number="settings.active_max_calibration_hours"
            type="number"
            min="1"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset">
          <SettingLabel
            label="重置前强制读取（分钟）"
            help="进入预计周限重置时间之前的该分钟范围后，强制读取快照，用于捕捉重置边界。"
          />
          <input
            v-model.number="settings.reset_proximity_minutes"
            type="number"
            min="5"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset">
          <SettingLabel
            label="快照陈旧警告（小时）"
            help="上游额度快照超过该时长未更新时，首页采集状态会标记为“快照陈旧”。它只影响告警展示，不会提高探测频率。"
          />
          <input
            v-model.number="settings.stale_warning_hours"
            type="number"
            min="1"
            class="input w-full"
          />
        </fieldset>
      </div>
      <div class="flex items-center justify-between">
        <SettingLabel
          label="启用后台监控"
          help="开启后由容器内 Django 后台进程按本地探测间隔运行；关闭后不会自动探测，手动“立即测算”仍可使用。"
        />
        <input
          v-model="settings.monitoring_enabled"
          type="checkbox"
          class="toggle toggle-sm"
        />
      </div>
      <button
        class="btn btn-primary btn-sm"
        :disabled="saving"
        @click="emit('save')"
      >
        <span v-if="saving" class="loading loading-xs loading-spinner"></span>
        <AppIcon v-else name="check" class="size-4" />保存采样策略
      </button>
    </div>
  </section>
</template>
