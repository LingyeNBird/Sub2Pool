<script setup lang="ts">
import type { BarSeriesOption, LineSeriesOption } from "echarts/charts";
import { BarChart, LineChart } from "echarts/charts";
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

import { useThemeStore } from "@/stores/theme";
import { formatCurrency } from "@/utils/formatters";

use([SVGRenderer, LineChart, BarChart, GridComponent, TooltipComponent]);

type ChartOption = ComposeOption<
  | LineSeriesOption
  | BarSeriesOption
  | GridComponentOption
  | TooltipComponentOption
>;

const props = withDefaults(
  defineProps<{
    kind: "line" | "bar";
    labels: string[];
    values: number[];
    min?: number | null;
    max?: number | null;
  }>(),
  {
    min: null,
    max: null,
  },
);

const theme = useThemeStore();
const numberFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 0,
});

const colors = computed(() => {
  const styles = getComputedStyle(document.documentElement);
  return {
    primary:
      styles.getPropertyValue("--color-primary").trim() ||
      (theme.current === "light" ? "#155dfc" : "#4f39f6"),
    content:
      styles.getPropertyValue("--color-base-content").trim() ||
      (theme.current === "light" ? "#1f2937" : "#f3f4f6"),
  };
});

const option = computed<ChartOption>(() => ({
  animationDuration: 250,
  grid: {
    top: 12,
    right: 12,
    bottom: 12,
    left: 8,
    containLabel: true,
  },
  tooltip: {
    trigger: "axis",
    confine: true,
    valueFormatter: (value) => formatCurrency(Number(value)),
  },
  xAxis: {
    type: "category",
    data: props.labels,
    boundaryGap: props.kind === "bar",
    axisTick: { show: false },
    axisLine: {
      lineStyle: { color: colors.value.content, opacity: 0.2 },
    },
    axisLabel: {
      color: colors.value.content,
      opacity: 0.6,
      hideOverlap: true,
      margin: 10,
    },
  },
  yAxis: {
    type: "value",
    min: props.min ?? undefined,
    max: props.max ?? undefined,
    scale: props.kind === "line",
    axisLabel: {
      color: colors.value.content,
      opacity: 0.6,
      formatter: (value: number) => `$${numberFormatter.format(value)}`,
    },
    splitLine: {
      lineStyle: { color: colors.value.content, opacity: 0.12 },
    },
  },
  series:
    props.kind === "line"
      ? [
          {
            type: "line",
            data: props.values,
            showSymbol: props.values.length <= 30,
            symbolSize: 6,
            lineStyle: { color: colors.value.primary, width: 2 },
            itemStyle: { color: colors.value.primary },
            // 主题主色使用 OKLCH。ECharts 的默认强调色计算无法稳定解析该格式，
            // 轴提示触发强调态时会把折线绘制成透明色，因此保留原始样式。
            emphasis: { disabled: true },
          },
        ]
      : [
          {
            type: "bar",
            data: props.values,
            barMaxWidth: 24,
            itemStyle: {
              color: colors.value.primary,
              borderRadius: [3, 3, 0, 0],
            },
            // 柱子的定位已由纵向轴指示线表达，不再叠加默认强调态。
            emphasis: { disabled: true },
          },
        ],
}));
</script>

<template>
  <div class="w-full shrink-0">
    <VChart
      class="h-full w-full"
      :option="option"
      :init-options="{ renderer: 'svg' }"
      autoresize
    />
  </div>
</template>
