<script setup lang="ts">
import { computed, ref } from "vue";

import type { StatisticsData, UsagePoint } from "@/types";
import { formatCurrency } from "@/utils/formatters";

import StatisticsChart from "./StatisticsChart.vue";
import UsageHeatmap from "./UsageHeatmap.vue";

const props = defineProps<{
  data: StatisticsData | null;
  loading: boolean;
}>();

const emit = defineEmits<{
  showApiUsage: [participantId: number, participantName: string];
}>();

const usageDays = defineModel<number>("days", { required: true });
const usagePrecision = defineModel<"raw" | "hour" | "day">("precision", {
  required: true,
});

const chartMode = ref<"bar" | "heatmap">("bar");

function usageDeltas(points: UsagePoint[]) {
  return points.slice(1).flatMap((point, index) => {
    const previous = points[index];
    const value =
      point.account_cycle_usage_usd - previous.account_cycle_usage_usd;
    // 累计值回落通常代表周期重置或数据校正，不能把跨界差值当作用量。
    return value >= 0 ? [{ label: point.label, value }] : [];
  });
}

const participantCharts = computed(() =>
  (props.data?.participant_series ?? []).map((series) => ({
    ...series,
    usagePoints: usageDeltas(series.points),
  })),
);

const usageIntervalLabel = computed(
  () =>
    ({
      raw: "每次探测新增用量",
      hour: "每小时新增用量",
      day: "每天新增用量",
    })[usagePrecision.value],
);
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="card-title">
            <AppIcon name="chart-bar" class="size-5" />参与者账号用量
            <span
              class="responsive-help-tooltip tooltip tooltip-bottom"
              :data-tip="`柱状图和热力图都使用相邻累计值相减，展示所选时间粒度内的新增用量；首个数据点没有前序基线，累计值回落的跨周期区间也不会绘制。热力图按当前范围内的最大增量动态划分主题色深浅。后台当前每 ${data?.sample_interval_minutes ?? '—'} 分钟探测一次。`"
            >
              <button
                type="button"
                class="btn btn-circle cursor-help btn-ghost btn-xs"
                aria-label="查看参与者账号用量说明"
              >
                ?
              </button>
            </span>
          </h2>
        </div>
        <div class="flex flex-wrap gap-2">
          <fieldset class="fieldset">
            <label class="label">时间范围</label>
            <select v-model.number="usageDays" class="select select-sm">
              <option :value="7">最近 7 天</option>
              <option :value="14">最近 14 天</option>
              <option :value="28">最近 28 天</option>
            </select>
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">图表精度</label>
            <select v-model="usagePrecision" class="select select-sm">
              <option value="raw">每次探测</option>
              <option value="hour">每小时末值</option>
              <option value="day">每天末值</option>
            </select>
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">展示方式</label>
            <div class="join">
              <button
                type="button"
                class="btn join-item btn-sm"
                :class="{ 'btn-active': chartMode === 'bar' }"
                @click="chartMode = 'bar'"
              >
                <AppIcon name="chart-bar" class="size-4" />柱状图
              </button>
              <button
                type="button"
                class="btn join-item btn-sm"
                :class="{ 'btn-active': chartMode === 'heatmap' }"
                @click="chartMode = 'heatmap'"
              >
                <AppIcon name="squares-2x2" class="size-4" />热力图
              </button>
            </div>
          </fieldset>
        </div>
      </div>

      <div v-if="loading" class="flex justify-center py-16">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div
        v-else-if="participantCharts.length"
        class="grid gap-4 xl:grid-cols-2"
      >
        <article
          v-for="series in participantCharts"
          :key="series.participant_id"
          class="rounded-box border border-base-300 bg-base-100 p-5"
        >
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h3 class="font-semibold">{{ series.participant_name }}</h3>
              <p class="text-sm opacity-60">
                Sub2API 账号 {{ series.sub2api_user_id }}
              </p>
            </div>
            <div class="text-right text-sm">
              <div>
                账号本周期用量：{{
                  formatCurrency(series.points.at(-1)?.account_cycle_usage_usd)
                }}
              </div>
              <div class="opacity-60">
                当前用户余额：{{
                  formatCurrency(series.points.at(-1)?.balance_usd)
                }}
              </div>
              <button
                type="button"
                class="btn mt-2 btn-outline btn-xs"
                @click="
                  emit(
                    'showApiUsage',
                    series.participant_id,
                    series.participant_name,
                  )
                "
              >
                <AppIcon name="chart-pie" class="size-4" />API 用量构成
              </button>
            </div>
          </div>
          <p class="mt-4 text-xs font-medium opacity-60">
            {{ usageIntervalLabel }}
          </p>
          <div class="mt-2 h-48 w-full">
            <StatisticsChart
              v-if="series.usagePoints.length && chartMode === 'bar'"
              class="h-full w-full"
              kind="bar"
              :values="series.usagePoints.map((item) => item.value)"
              :labels="series.usagePoints.map((item) => item.label)"
              :min="0"
            />
            <UsageHeatmap
              v-else-if="series.usagePoints.length"
              class="h-full w-full"
              :points="series.usagePoints"
              :precision="usagePrecision"
              :sample-interval-minutes="data?.sample_interval_minutes"
            />
            <div
              v-else
              class="flex h-full items-center justify-center text-sm opacity-60"
            >
              尚无可比较的相邻用量样本
            </div>
          </div>
        </article>
      </div>
      <div v-else class="py-16 text-center opacity-60">尚未添加参与者。</div>

      <div class="alert text-sm alert-info">
        <AppIcon name="information-circle" class="size-5" />
        <span>
          “图表精度”只改变展示聚合方式；实际采集频率由系统设置中的“本地探测间隔”控制。
        </span>
      </div>
    </div>
  </section>
</template>
