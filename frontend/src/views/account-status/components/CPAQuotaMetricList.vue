<script setup lang="ts">
import { computed } from "vue";
import type { CPAQuotaMetrics } from "@/types/accounts";
import { formatCurrency } from "@/utils/formatters";
const props = defineProps<{
  metrics?: CPAQuotaMetrics | null;
  prediction?: boolean;
  tiles?: boolean;
}>();
const compact = (value: number | undefined) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        notation: "compact",
        maximumFractionDigits: 1,
      }).format(value);
const rows = computed(() =>
  [
    {
      label: props.prediction ? "预计请求" : "请求数",
      value: compact(props.metrics?.request_count),
      icon: "chart-bar",
      tone: "text-info",
      background: "bg-info/10",
    },
    {
      label: props.prediction ? "预计 Token" : "Token 数",
      value: compact(props.metrics?.token_count),
      icon: "cpu-chip",
      tone: "text-accent",
      background: "bg-accent/10",
    },
    {
      label: "估算费用",
      value: props.metrics ? formatCurrency(props.metrics.usage_usd) : "—",
      icon: "currency-dollar",
      tone: "text-warning",
      background: "bg-warning/10",
    },
    {
      label: "成功率",
      value:
        props.metrics?.success_rate == null
          ? "—"
          : `${props.metrics.success_rate.toFixed(2)}%`,
      icon: "check-circle",
      tone: "text-success",
      background: "bg-success/10",
    },
  ].filter((row) => !props.prediction || row.label !== "成功率"),
);
</script>
<template>
  <dl :class="tiles ? 'grid grid-cols-2 gap-3 xl:grid-cols-4' : 'space-y-5'">
    <div
      v-for="row in rows"
      :key="row.label"
      :class="
        tiles
          ? 'card border-base-300 bg-base-100 card-border'
          : 'flex flex-wrap items-center justify-between gap-2'
      "
    >
      <div :class="tiles ? 'card-body gap-3 p-4 sm:p-5' : 'contents'">
        <dt class="flex items-center gap-2 text-sm text-base-content/60">
          <span class="inline-flex rounded-box p-2" :class="row.background"
            ><AppIcon :name="row.icon" class="size-5" :class="row.tone" /></span
          >{{ row.label }}
        </dt>
        <dd
          class="font-semibold tabular-nums"
          :class="tiles ? 'text-2xl sm:text-3xl' : 'text-base'"
        >
          {{ row.value }}
        </dd>
      </div>
    </div>
  </dl>
  <p
    v-if="!tiles && metrics?.unpriced_request_count"
    class="text-xs text-base-content/70"
  >
    {{ metrics.unpriced_request_count }} 次请求缺少价格，未计入估算费用。
  </p>
</template>
