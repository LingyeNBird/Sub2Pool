<script setup lang="ts">
import type { PieSeriesOption } from "echarts/charts";
import { PieChart } from "echarts/charts";
import type { TooltipComponentOption } from "echarts/components";
import { TooltipComponent } from "echarts/components";
import type { ComposeOption } from "echarts/core";
import { use } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { computed } from "vue";
import VChart from "vue-echarts";

import type { APIKeyUsageItem } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

use([SVGRenderer, PieChart, TooltipComponent]);

type ChartOption = ComposeOption<PieSeriesOption | TooltipComponentOption>;

const props = defineProps<{
  items: APIKeyUsageItem[];
}>();

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

const option = computed<ChartOption>(() => ({
  animationDuration: 250,
  tooltip: {
    trigger: "item",
    confine: true,
    formatter: (params) => {
      const point = Array.isArray(params) ? params[0] : params;
      const item = props.items[Number(point?.dataIndex)];
      if (!item) return "";
      return [
        `<strong>${escapeHTML(item.name)}</strong>`,
        `用量：${formatCurrency(item.usage_usd)}`,
        `占参与者用量：${formatPercent(item.participant_usage_percent)}`,
        `占总周限：${formatPercent(item.weekly_quota_percent)}`,
      ].join("<br>");
    },
  },
  series: [
    {
      type: "pie",
      radius: ["52%", "78%"],
      avoidLabelOverlap: true,
      minAngle: 3,
      itemStyle: {
        borderColor: "transparent",
        borderWidth: 2,
        borderRadius: 4,
      },
      label: {
        formatter: "{b}\n{d}%",
      },
      emphasis: {
        scaleSize: 6,
      },
      data: props.items.map((item) => ({
        name: item.name,
        value: item.usage_usd,
      })),
    },
  ],
}));
</script>

<template>
  <VChart
    class="h-full w-full"
    :option="option"
    :init-options="{ renderer: 'svg' }"
    autoresize
  />
</template>
