<script setup lang="ts">
import { computed, ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import type { CapacityPoint, StatisticsData } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

import StatisticsChart from "./StatisticsChart.vue";

type HistoryEstimateMode = "cycle" | "daily";

interface CapacityHistoryEntry {
  point: CapacityPoint;
  value: number;
}

const props = defineProps<{
  data: StatisticsData | null;
  loading: boolean;
}>();

const emit = defineEmits<{
  showBasis: [kind: "cycle" | "today"];
  showClosingBasis: [point: CapacityPoint, kind: HistoryEstimateMode];
}>();

const capacityPeriod = defineModel<"day" | "month">("period", {
  required: true,
});
const capacityDays = defineModel<number>("days", { required: true });
const historyEstimateMode = ref<HistoryEstimateMode>("cycle");
const dateTime = useDateTime();
const capacityHistory = computed<CapacityHistoryEntry[]>(() =>
  (props.data?.capacity_series ?? []).flatMap((point) => {
    const value =
      historyEstimateMode.value === "cycle"
        ? point.weekly_total_usd
        : point.daily_total_usd;
    return value === null ? [] : [{ point, value }];
  }),
);
const capacityValues = computed(() =>
  capacityHistory.value.map((entry) => entry.value),
);
const capacityLabels = computed(() =>
  capacityHistory.value.map((entry) => entry.point.period),
);
const capacityScale = computed(() => {
  if (!capacityValues.value.length) return { min: null, max: null };
  const minimum = Math.min(...capacityValues.value);
  const maximum = Math.max(...capacityValues.value);
  const span = maximum - minimum;
  const padding =
    span > 0 ? Math.max(span * 0.1, 1) : Math.max(Math.abs(minimum) * 0.05, 1);
  return {
    min: Math.max(0, minimum - padding),
    max: maximum + padding,
  };
});

const historyTitle = computed(() =>
  historyEstimateMode.value === "cycle"
    ? "本周期累计折算的每日收盘历史"
    : "日内增量折算的每日历史",
);
const historyDescription = computed(() =>
  historyEstimateMode.value === "cycle"
    ? "日视图取当天最后一次从周期起点累计得到的估算，可点击折线点查看当日依据；月视图取每日收盘估算的平均值。"
    : "日视图使用同一天、同一归属区间内首末观测的增量估算，可点击折线点查看区间依据；月视图取有效日内估算的平均值。",
);
const historyEmptyMessage = computed(() =>
  historyEstimateMode.value === "cycle"
    ? "尚无累计口径观测，完成首次测算后才会形成周限总额度历史。"
    : "当前范围内没有达到最小周限跨度的日内估算。",
);

function showClosingBasis(index: number) {
  const entry = capacityHistory.value[index];
  if (!entry || capacityPeriod.value !== "day") return;
  const hasBasis =
    historyEstimateMode.value === "cycle"
      ? entry.point.basis !== null
      : entry.point.daily_basis !== null;
  if (hasBasis) {
    emit("showClosingBasis", entry.point, historyEstimateMode.value);
  }
}
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
              class="responsive-help-tooltip tooltip tooltip-bottom"
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

      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h3 class="font-semibold">{{ historyTitle }}</h3>
          <p class="mt-1 text-sm opacity-60">
            {{ historyDescription }}
          </p>
        </div>
        <div class="join shrink-0">
          <button
            type="button"
            class="btn join-item btn-sm"
            :class="{ 'btn-active': historyEstimateMode === 'cycle' }"
            @click="historyEstimateMode = 'cycle'"
          >
            累计收盘
          </button>
          <button
            type="button"
            class="btn join-item btn-sm"
            :class="{ 'btn-active': historyEstimateMode === 'daily' }"
            @click="historyEstimateMode = 'daily'"
          >
            日内折算
          </button>
        </div>
      </div>
      <div v-if="loading" class="flex justify-center py-16">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <StatisticsChart
        v-else-if="capacityValues.length"
        class="h-64 w-full"
        kind="line"
        :values="capacityValues"
        :labels="capacityLabels"
        :min="capacityScale.min"
        :max="capacityScale.max"
        :clickable="capacityPeriod === 'day'"
        @point-click="showClosingBasis"
      />
      <div v-else class="py-16 text-center opacity-60">
        {{ historyEmptyMessage }}
      </div>
    </div>
  </section>
</template>
