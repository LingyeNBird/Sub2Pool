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
        <AppIcon name="calculator" class="size-5" />分配模型
      </h2>
      <fieldset class="fieldset max-w-full min-w-0 grid-cols-[minmax(0,1fr)]">
        <SettingLabel
          label="周限额度模型"
          help="只控制参与者归属和余额建议的展示口径。时变额度使用整数百分比、全用户成本与连续容量模型生成概率估计，并用确定性边界限制建议；平均恒定使用周期累计成本与官方已用百分比计算。额度统计中的累计折算和日内折算始终采用简单端点公式，不受这里切换影响。"
        />
        <select
          v-model="settings.weekly_quota_model"
          class="select w-full max-w-full min-w-0"
        >
          <option value="time_varying">时变额度</option>
          <option value="constant_average">平均恒定</option>
        </select>
      </fieldset>
      <fieldset class="fieldset max-w-full min-w-0 grid-cols-[minmax(0,1fr)]">
        <SettingLabel
          label="成本口径"
          help="实际扣费会应用 Sub2API 的计费倍率，并与用户余额真实扣减一致；标准计费只按模型标准单价计算。余额分配建议通常应选择实际扣费。"
        />
        <select
          v-model="settings.cost_basis"
          class="select w-full max-w-full min-w-0"
        >
          <option value="actual">实际扣费（推荐）</option>
          <option value="standard">标准计费</option>
        </select>
      </fieldset>
      <div class="grid gap-3 md:grid-cols-2">
        <fieldset class="fieldset min-w-0">
          <SettingLabel
            label="无样本时美元 / 1%"
            help="账号尚无模型历史时，用该数值估算自动采样的成本触发阈值；形成正式观测后，时变归属与余额建议由观测数据计算。"
          />
          <input
            v-model.number="settings.initial_usd_per_percent"
            type="number"
            min="0"
            step="0.01"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset min-w-0">
          <SettingLabel
            label="安全系数"
            help="余额建议 = 剩余百分比权益 × 当前展示模型采用的美元/1% × 安全系数。小于 1 会预留安全余量，降低超分配风险。"
          />
          <input
            v-model.number="settings.safety_factor"
            type="number"
            min="0.1"
            max="1"
            step="0.01"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset min-w-0 grid-cols-[minmax(0,1fr)]">
          <SettingLabel
            label="日估算最小周限跨度（%）"
            help="今日观测至少跨过该百分比后才给出日估算，避免 OpenAI 只返回整数周限而造成过大误差。"
          />
          <input
            v-model.number="settings.daily_estimate_min_percent_span"
            type="number"
            min="1"
            max="100"
            step="1"
            class="input w-full max-w-full min-w-0"
          />
        </fieldset>
        <fieldset class="fieldset min-w-0">
          <SettingLabel
            label="建议差额阈值（美元）"
            help="建议余额与当前 Sub2API 用户余额的绝对差达到该金额后，标记为“建议调整”，并可触发对应通知。"
          />
          <input
            v-model.number="settings.recommendation_change_usd"
            type="number"
            min="0"
            step="0.01"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset min-w-0">
          <SettingLabel
            label="用户余额耗尽预警余量（美元）"
            help="Sub2API 用户余额低于或等于该金额时视为接近耗尽；若仍有剩余权益，会触发校准并可发送提醒。"
          />
          <input
            v-model.number="settings.limit_warning_usd"
            type="number"
            min="0"
            step="0.01"
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
        <AppIcon v-else name="check" class="size-4" />保存分配模型
      </button>
    </div>
  </section>
</template>
