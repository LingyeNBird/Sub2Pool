<script setup lang="ts">
import { computed } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import type { StatisticsData } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

const props = defineProps<{
  data: StatisticsData | null;
  loading: boolean;
}>();

const emit = defineEmits<{
  showBasis: [kind: "cycle" | "today"];
}>();

const capacityPeriod = defineModel<"day" | "month">("period", {
  required: true,
});
const capacityDays = defineModel<number>("days", { required: true });
const dateTime = useDateTime();
const capacityValues = computed(
  () => props.data?.capacity_series.map((item) => item.weekly_total_usd) ?? [],
);
const capacityLabels = computed(
  () => props.data?.capacity_series.map((item) => item.period) ?? [],
);
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="card-title">
            <AppIcon
              name="presentation-chart-line"
              class="size-5"
            />周限等效额度估算
            <span
              class="tooltip tooltip-right"
              data-tip="本周期估算使用官方七天周期累计用量；今日估算只使用今天已覆盖观测区间的增量。"
            >
              <button
                type="button"
                class="btn btn-circle cursor-help btn-ghost btn-xs"
                aria-label="查看周限等效额度估算说明"
              >
                ?
              </button>
            </span>
          </h2>
        </div>
        <div class="flex flex-wrap gap-2">
          <fieldset class="fieldset">
            <label class="label">历史统计维度</label>
            <select v-model="capacityPeriod" class="select select-sm">
              <option value="day">按天</option>
              <option value="month">按月</option>
            </select>
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">查看范围</label>
            <select v-model.number="capacityDays" class="select select-sm">
              <option :value="30">最近 30 天</option>
              <option :value="90">最近 90 天</option>
              <option :value="365">最近 1 年</option>
              <option :value="730">最近 2 年</option>
            </select>
          </fieldset>
        </div>
      </div>

      <div class="stats stats-vertical bg-base-100 xl:stats-horizontal">
        <div class="stat">
          <div class="stat-title">本周期累计折算</div>
          <div class="flex items-baseline gap-2">
            <div class="stat-value text-xl font-semibold">
              {{ formatCurrency(data?.capacity_summary.cycle?.estimate_usd) }}
            </div>
            <button
              v-if="data?.capacity_summary.cycle?.rate_calculated"
              type="button"
              class="cursor-pointer text-xs underline underline-offset-2 opacity-50"
              @click="emit('showBasis', 'cycle')"
            >
              查看依据
            </button>
          </div>
          <div class="stat-desc">
            累计
            {{ formatCurrency(data?.capacity_summary.cycle?.cost_usd) }} /
            {{ formatPercent(data?.capacity_summary.cycle?.used_percent) }} ·
            置信度
            {{ data?.capacity_summary.cycle?.confidence ?? "—" }}
          </div>
        </div>
        <div class="stat">
          <div class="stat-title">今日用量折算</div>
          <div class="flex items-baseline gap-2">
            <div class="stat-value text-xl font-semibold">
              {{
                data?.capacity_summary.today.sufficient
                  ? formatCurrency(data.capacity_summary.today.estimate_usd)
                  : "样本不足"
              }}
            </div>
            <button
              v-if="data?.capacity_summary.today.sufficient"
              type="button"
              class="cursor-pointer text-xs underline underline-offset-2 opacity-50"
              @click="emit('showBasis', 'today')"
            >
              查看依据
            </button>
          </div>
          <div class="stat-desc">
            需要至少跨过
            {{ formatPercent(data?.capacity_summary.today.min_percent_span) }}
          </div>
        </div>
        <div class="stat">
          <div class="stat-title">今日已覆盖观测区间</div>
          <div class="stat-value text-xl font-semibold">
            {{ formatPercent(data?.capacity_summary.today.percent_delta) }}
          </div>
          <div class="stat-desc">
            成本增量
            {{ formatCurrency(data?.capacity_summary.today.cost_delta_usd) }}
          </div>
        </div>
      </div>

      <div class="alert text-sm alert-info">
        <AppIcon name="information-circle" class="size-5" />
        <span>
          {{ data?.capacity_summary.today.reason }}
          <template v-if="data?.capacity_summary.today.observed_from">
            · 覆盖
            {{ dateTime(data.capacity_summary.today.observed_from) }} 至
            {{ dateTime(data.capacity_summary.today.observed_to) }}
          </template>
        </span>
      </div>

      <div>
        <h3 class="font-semibold">本周期累计估算的每日收盘历史</h3>
        <p class="mt-1 text-sm opacity-60">
          日视图取当天最后一次累计估算；月视图取每日收盘估算的平均值，不把日内每次探测当作独立结论。
        </p>
      </div>
      <div v-if="loading" class="flex justify-center py-16">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <tc-line
        v-else-if="capacityValues.length"
        class="block h-64 w-full"
        :values="capacityValues"
        :labels="capacityLabels"
        :min="0"
        tooltip="@L · $@V"
      ></tc-line>
      <div v-else class="py-16 text-center opacity-60">
        尚无累计口径观测，完成首次测算后才会形成周限总额度历史。
      </div>
    </div>
  </section>
</template>
