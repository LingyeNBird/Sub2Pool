<script setup lang="ts">
import type { BarSeriesOption } from "echarts/charts";
import { BarChart } from "echarts/charts";
import type {
  GridComponentOption,
  TooltipComponentOption,
} from "echarts/components";
import { GridComponent, TooltipComponent } from "echarts/components";
import type { ComposeOption } from "echarts/core";
import { use } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { computed } from "vue";
import VChart from "vue-echarts";

import { useAuthStore } from "@/stores/auth";
import { useThemeStore } from "@/stores/theme";
import type { AccountCycleUsage } from "@/types/accounts";
import { formatCurrency, formatPercent } from "@/utils/formatters";

use([SVGRenderer, BarChart, GridComponent, TooltipComponent]);

type ChartOption = ComposeOption<
  BarSeriesOption | GridComponentOption | TooltipComponentOption
>;

const props = defineProps<{
  items: AccountCycleUsage[];
}>();

const auth = useAuthStore();
const theme = useThemeStore();

const chartWidth = computed(
  () => `${Math.max(360, props.items.length * 42)}px`,
);

const colors = computed(() => {
  const styles = getComputedStyle(document.documentElement);
  return {
    used:
      styles.getPropertyValue("--color-success").trim() ||
      (theme.current === "light" ? "#00a63e" : "#05df72"),
    remaining: "#f97316",
    content:
      styles.getPropertyValue("--color-base-content").trim() ||
      (theme.current === "light" ? "#1f2937" : "#f3f4f6"),
    border:
      theme.current === "light"
        ? "rgba(31, 41, 55, 0.32)"
        : "rgba(255, 255, 255, 0.34)",
  };
});

function cycleDate(value: string): string {
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return "日期未知";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: auth.timezone,
      year: "numeric",
      month: "numeric",
      day: "numeric",
    }).format(instant);
  } catch {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "UTC",
      year: "numeric",
      month: "numeric",
      day: "numeric",
    }).format(instant);
  }
}

function escapeHTML(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character] ?? character,
  );
}
function cycleUsedPercent(item: AccountCycleUsage): number {
  return Math.min(100, Math.max(0, item.used_percent));
}

const option = computed<ChartOption>(() => ({
  animationDuration: 250,
  grid: {
    top: 12,
    right: 12,
    bottom: 4,
    left: 12,
    containLabel: true,
  },
  tooltip: {
    trigger: "axis",
    confine: true,
    axisPointer: { type: "shadow" },
    formatter: (params) => {
      const point = Array.isArray(params) ? params[0] : params;
      const item = props.items[Number(point?.dataIndex)];
      if (!item) return "";
      const dateRange = `${cycleDate(item.started_at)}—${cycleDate(item.ended_at)}`;
      return [
        `<strong>第 ${item.sequence} 周期</strong>`,
        `周期：${escapeHTML(dateRange)}`,
        `消耗：${formatPercent(item.used_percent)}`,
        `折算额度：${formatCurrency(item.used_usd)}`,
      ].join("<br>");
    },
  },
  xAxis: {
    type: "category",
    data: props.items.map((item) => `第 ${item.sequence} 周期`),
    axisTick: { show: false },
    axisLine: {
      lineStyle: { color: colors.value.content, opacity: 0.2 },
    },
    axisLabel: {
      color: colors.value.content,
      opacity: 0.65,
      interval: 0,
      rotate: 38,
      margin: 8,
    },
  },
  yAxis: {
    type: "value",
    min: 0,
    max: 100,
    interval: 25,
    axisLabel: {
      color: colors.value.content,
      opacity: 0.65,
      formatter: (value: number) => `${value}%`,
    },
    splitLine: {
      lineStyle: { color: colors.value.content, opacity: 0.12 },
    },
  },
  series: [
    {
      name: "已使用",
      type: "bar",
      stack: "cycle",
      barWidth: 24,
      data: props.items.map((item) => {
        const value = cycleUsedPercent(item);
        return {
          value,
          itemStyle: {
            borderRadius: value >= 100 ? 5 : [0, 0, 5, 5],
          },
        };
      }),
      itemStyle: {
        color: colors.value.used,
        borderColor: colors.value.border,
        borderWidth: 1,
      },
      emphasis: { disabled: true },
    },
    {
      name: "未使用",
      type: "bar",
      stack: "cycle",
      barWidth: 24,
      data: props.items.map((item) => {
        const used = cycleUsedPercent(item);
        return {
          value: 100 - used,
          itemStyle: {
            borderRadius: used <= 0 ? 5 : [5, 5, 0, 0],
          },
        };
      }),
      itemStyle: {
        color: colors.value.remaining,
        borderColor: colors.value.border,
        borderWidth: 1,
      },
      emphasis: { disabled: true },
    },
  ],
}));
</script>

<template>
  <div
    class="w-full min-w-0 overflow-x-auto pb-2"
    role="region"
    aria-label="历史周期消耗百分比柱状图，可横向滚动"
    tabindex="0"
  >
    <div class="h-64" :style="{ width: chartWidth }">
      <VChart
        class="h-full w-full"
        :option="option"
        :init-options="{ renderer: 'svg' }"
        autoresize
      />
    </div>
  </div>
</template>
