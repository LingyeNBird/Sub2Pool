<script setup lang="ts">
import type { BarSeriesOption } from "echarts/charts";
import { BarChart } from "echarts/charts";
import type {
  BrushComponentOption,
  GridComponentOption,
  TooltipComponentOption,
} from "echarts/components";
import {
  BrushComponent,
  GridComponent,
  TooltipComponent,
} from "echarts/components";
import type { ComposeOption } from "echarts/core";
import { use } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import VChart from "vue-echarts";

import { useAuthStore } from "@/stores/auth";
import { useThemeStore } from "@/stores/theme";
import type { AccountCycleUsage } from "@/types/accounts";
import { formatCurrency, formatPercent } from "@/utils/formatters";

use([SVGRenderer, BarChart, BrushComponent, GridComponent, TooltipComponent]);

type ChartOption = ComposeOption<
  | BarSeriesOption
  | BrushComponentOption
  | GridComponentOption
  | TooltipComponentOption
>;

const props = defineProps<{
  items: AccountCycleUsage[];
}>();

interface SelectionSummary {
  cycleRange: string;
  dateRange: string;
  usedPercent: number | null;
  usedUsd: number;
  estimatedTotalUsd: number | null;
}

interface SelectionMarker {
  index: number;
  sequence: number;
  left: number;
  top: number;
}

const BAR_WIDTH = 24;

const auth = useAuthStore();
const theme = useThemeStore();

const root = ref<HTMLElement | null>(null);
const scrollContainer = ref<HTMLElement | null>(null);
const chart = ref<InstanceType<typeof VChart> | null>(null);
const pendingSelectedIndices = ref<number[]>([]);
const selectedIndices = ref<number[]>([]);
const selectionMarkers = ref<SelectionMarker[]>([]);
const summaryLeft = ref<number | null>(null);
const summaryTop = ref<number | null>(null);
let brushActivated = false;
let brushActivationFrame: number | null = null;
let selectionFrame: number | null = null;
let markerFrame: number | null = null;
let tooltipFrame: number | null = null;
const selectionGestureActive = ref(false);
let dragOrigin: { x: number; y: number } | null = null;
let draggedFarEnough = false;

const markerIndices = computed(() =>
  selectionGestureActive.value
    ? pendingSelectedIndices.value
    : selectedIndices.value,
);

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

const selectionSummary = computed<SelectionSummary | null>(() => {
  const selectedItems = selectedIndices.value.flatMap((index) => {
    const item = props.items[index];
    return item ? [item] : [];
  });
  if (!selectedItems.length) return null;

  const first = selectedItems[0];
  const last = selectedItems[selectedItems.length - 1];
  if (!first || !last) return null;

  const usedUsd = selectedItems.reduce(
    (total, item) =>
      total + (Number.isFinite(item.used_usd) ? item.used_usd : 0),
    0,
  );
  const inferredCapacities = selectedItems.flatMap((item) =>
    Number.isFinite(item.used_percent) &&
    item.used_percent > 0 &&
    Number.isFinite(item.used_usd)
      ? [item.used_usd / (item.used_percent / 100)]
      : [],
  );
  const averageCapacity = inferredCapacities.length
    ? inferredCapacities.reduce((total, value) => total + value, 0) /
      inferredCapacities.length
    : null;
  const estimatedTotalUsd =
    averageCapacity === null ? null : averageCapacity * selectedItems.length;

  return {
    cycleRange:
      first.sequence === last.sequence
        ? `第 ${first.sequence} 周期`
        : `第 ${first.sequence} 周期至第 ${last.sequence} 周期`,
    dateRange: `${cycleDate(first.started_at)}—${cycleDate(last.ended_at)}`,
    usedPercent:
      estimatedTotalUsd && estimatedTotalUsd > 0
        ? (usedUsd / estimatedTotalUsd) * 100
        : null,
    usedUsd,
    estimatedTotalUsd,
  };
});

function activateBrush(): void {
  if (brushActivationFrame !== null || brushActivated || !chart.value) return;
  brushActivated = true;
  chart.value.dispatchAction({
    type: "takeGlobalCursor",
    key: "brush",
    brushOption: {
      brushType: "rect",
      brushMode: "single",
    },
  });
}

function hideAxisTooltip(): void {
  chart.value?.dispatchAction({ type: "hideTip" });
  if (tooltipFrame !== null) cancelAnimationFrame(tooltipFrame);
  tooltipFrame = requestAnimationFrame(() => {
    chart.value?.dispatchAction({ type: "hideTip" });
    tooltipFrame = null;
  });
}

function clearBrushArea(): void {
  chart.value?.dispatchAction({ type: "brush", areas: [] });
}

function rearmBrush(): void {
  if (brushActivationFrame !== null) {
    cancelAnimationFrame(brushActivationFrame);
  }
  brushActivated = false;
  brushActivationFrame = requestAnimationFrame(() => {
    brushActivationFrame = null;
    activateBrush();
  });
  chart.value?.dispatchAction({ type: "takeGlobalCursor" });
  clearBrushArea();
}

function clearSelectedOutput(): void {
  pendingSelectedIndices.value = [];
  selectedIndices.value = [];
  selectionMarkers.value = [];
  summaryLeft.value = null;
  summaryTop.value = null;
}

function clearSelection(): void {
  if (selectionFrame !== null) cancelAnimationFrame(selectionFrame);
  selectionFrame = null;
  selectionGestureActive.value = false;
  dragOrigin = null;
  draggedFarEnough = false;
  clearSelectedOutput();
  hideAxisTooltip();
  rearmBrush();
}

function beginSelection(params: unknown): void {
  if (!(params instanceof PointerEvent) || params.button !== 0) return;
  if (selectionFrame !== null) cancelAnimationFrame(selectionFrame);
  selectionFrame = null;
  selectionGestureActive.value = true;
  dragOrigin = { x: params.clientX, y: params.clientY };
  draggedFarEnough = false;
  pendingSelectedIndices.value = [];
  updateSelectionMarkers();
  hideAxisTooltip();
}

function updatePendingSelection(clientX: number): void {
  if (!chart.value || !dragOrigin) return;
  const chartRect = chart.value.getDom().getBoundingClientRect();
  const minimumX = Math.min(dragOrigin.x, clientX);
  const maximumX = Math.max(dragOrigin.x, clientX);
  const halfBarWidth = BAR_WIDTH / 2;
  pendingSelectedIndices.value = props.items.flatMap((item, index) => {
    const chartX = chart.value?.convertToPixel(
      { xAxisIndex: 0 },
      `第 ${item.sequence} 周期`,
    );
    if (typeof chartX !== "number" || !Number.isFinite(chartX)) return [];
    const centerX = chartRect.left + chartX;
    return centerX + halfBarWidth >= minimumX &&
      centerX - halfBarWidth <= maximumX
      ? [index]
      : [];
  });
  updateSelectionMarkers();
}

function trackSelectionPointer(params: unknown): void {
  if (
    !selectionGestureActive.value ||
    !dragOrigin ||
    !(params instanceof PointerEvent)
  ) {
    return;
  }
  hideAxisTooltip();
  if ((params.buttons & 1) === 0) return;
  if (
    Math.hypot(params.clientX - dragOrigin.x, params.clientY - dragOrigin.y) >=
    4
  ) {
    draggedFarEnough = true;
    updatePendingSelection(params.clientX);
  }
}

function updateSelectionMarkers(): void {
  if (!chart.value || !markerIndices.value.length) {
    selectionMarkers.value = [];
    summaryLeft.value = null;
    summaryTop.value = null;
    return;
  }
  const topPixel = chart.value.convertToPixel({ yAxisIndex: 0 }, 100);
  if (typeof topPixel !== "number" || !Number.isFinite(topPixel)) return;
  const chartRect = chart.value.getDom().getBoundingClientRect();
  const visibleRect =
    scrollContainer.value?.getBoundingClientRect() ?? chartRect;
  const positions = markerIndices.value.flatMap((index) => {
    const item = props.items[index];
    if (!item) return [];
    const chartLeft = chart.value?.convertToPixel(
      { xAxisIndex: 0 },
      `第 ${item.sequence} 周期`,
    );
    if (typeof chartLeft !== "number" || !Number.isFinite(chartLeft)) return [];
    return [
      {
        index,
        sequence: item.sequence,
        left: chartRect.left + chartLeft,
        top: chartRect.top + topPixel - 6,
      },
    ];
  });
  selectionMarkers.value = positions.filter(
    (marker) =>
      marker.left >= visibleRect.left && marker.left <= visibleRect.right,
  );
  const first = positions[0];
  const last = positions[positions.length - 1];
  if (!first || !last) {
    summaryLeft.value = null;
    summaryTop.value = null;
    return;
  }
  const halfTooltipWidth = Math.min(160, visibleRect.width / 2);
  const minimumLeft = visibleRect.left + halfTooltipWidth;
  const maximumLeft = visibleRect.right - halfTooltipWidth;
  const selectedCenter = (first.left + last.left) / 2;
  summaryLeft.value = Math.min(
    maximumLeft,
    Math.max(minimumLeft, selectedCenter),
  );
  summaryTop.value = chartRect.top + topPixel - 32;
}

function scheduleSelectionMarkers(): void {
  if (markerFrame !== null) cancelAnimationFrame(markerFrame);
  void nextTick(() => {
    markerFrame = requestAnimationFrame(() => {
      updateSelectionMarkers();
      markerFrame = null;
    });
  });
}

function finishSelection(): void {
  if (!selectionGestureActive.value || selectionFrame !== null) return;
  const shouldCommit = draggedFarEnough;
  dragOrigin = null;
  draggedFarEnough = false;
  hideAxisTooltip();
  selectionFrame = requestAnimationFrame(() => {
    const committedIndices = shouldCommit
      ? [...pendingSelectedIndices.value]
      : [];
    selectedIndices.value = committedIndices;
    pendingSelectedIndices.value = [];
    selectionGestureActive.value = false;
    rearmBrush();
    scheduleSelectionMarkers();
    selectionFrame = null;
  });
}

function handleDocumentPointerDown(event: PointerEvent): void {
  const target = event.target;
  if (target instanceof Node && root.value?.contains(target)) return;
  if (selectedIndices.value.length) clearSelection();
}

watch(
  () => props.items,
  () => clearSelection(),
);

onMounted(() => {
  document.addEventListener("pointerdown", handleDocumentPointerDown);
  document.addEventListener("pointerup", finishSelection);
  window.addEventListener("resize", scheduleSelectionMarkers);
  window.addEventListener("scroll", scheduleSelectionMarkers, true);
});

onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", handleDocumentPointerDown);
  document.removeEventListener("pointerup", finishSelection);
  window.removeEventListener("resize", scheduleSelectionMarkers);
  window.removeEventListener("scroll", scheduleSelectionMarkers, true);
  if (selectionFrame !== null) cancelAnimationFrame(selectionFrame);
  if (brushActivationFrame !== null) cancelAnimationFrame(brushActivationFrame);
  if (markerFrame !== null) cancelAnimationFrame(markerFrame);
  if (tooltipFrame !== null) cancelAnimationFrame(tooltipFrame);
});

const option = computed<ChartOption>(() => ({
  animationDuration: 250,
  grid: {
    top: 12,
    right: 12,
    bottom: 4,
    left: 12,
    containLabel: true,
  },
  brush: {
    toolbox: [],
    xAxisIndex: 0,
    seriesIndex: [0, 1],
    brushLink: [0, 1],
    brushType: "rect",
    brushMode: "single",
    transformable: false,
    removeOnClick: false,
    throttleType: "fixRate",
    throttleDelay: 0,
    brushStyle: {
      borderWidth: 2,
      borderColor: "#3b82f6",
      color: "rgba(59, 130, 246, 0.16)",
    },
    inBrush: { opacity: 1 },
    outOfBrush: { opacity: 0.38 },
  },
  tooltip: {
    show: selectionSummary.value === null,
    trigger: "axis",
    confine: true,
    axisPointer: { type: "shadow" },
    formatter: (params) => {
      if (selectionGestureActive.value) return "";
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
      barWidth: BAR_WIDTH,
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
      barWidth: BAR_WIDTH,
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
  <div ref="root" class="relative w-full min-w-0">
    <div
      ref="scrollContainer"
      class="w-full min-w-0 overflow-x-auto pb-2"
      role="region"
      aria-label="历史周期消耗百分比柱状图，可横向滚动，也可按住鼠标左键拖动框选周期"
      tabindex="0"
    >
      <div class="h-64" :style="{ width: chartWidth }">
        <VChart
          ref="chart"
          class="h-full w-full cursor-crosshair select-none"
          :option="option"
          :init-options="{ renderer: 'svg' }"
          autoresize
          @brushend="finishSelection"
          @finished="activateBrush"
          @rendered="scheduleSelectionMarkers"
          @native:pointerdown="beginSelection"
          @native:pointermove="trackSelectionPointer"
        />
      </div>
    </div>

    <Teleport to="body">
      <span
        v-for="marker in selectionMarkers"
        :key="marker.index"
        class="pointer-events-none fixed z-[100] flex size-5 -translate-x-1/2 -translate-y-full items-center justify-center rounded-full border-2 border-base-100 bg-success text-[11px] font-black text-success-content"
        :style="{ left: `${marker.left}px`, top: `${marker.top}px` }"
        aria-hidden="true"
      >
        ✓
      </span>

      <div
        v-if="
          !selectionGestureActive &&
          selectionSummary &&
          summaryLeft !== null &&
          summaryTop !== null
        "
        class="pointer-events-none fixed z-[110] -translate-x-1/2"
        :style="{ left: `${summaryLeft}px`, top: `${summaryTop}px` }"
        role="status"
        aria-live="polite"
      >
        <div class="tooltip tooltip-top tooltip-open">
          <div
            class="tooltip-content w-max max-w-80 px-3 py-2 text-left text-xs"
          >
            <div class="font-semibold">{{ selectionSummary.cycleRange }}</div>
            <div class="mt-0.5 opacity-60">
              {{ selectionSummary.dateRange }}
            </div>
            <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 tabular-nums">
              <span>
                总消耗
                <strong>{{
                  formatPercent(selectionSummary.usedPercent)
                }}</strong>
              </span>
              <span>
                折算额度
                <strong>{{ formatCurrency(selectionSummary.usedUsd) }}</strong>
              </span>
              <span>
                预计总额度
                <strong>{{
                  formatCurrency(selectionSummary.estimatedTotalUsd)
                }}</strong>
              </span>
            </div>
          </div>
          <span class="block size-1" aria-hidden="true"></span>
        </div>
      </div>
    </Teleport>
  </div>
</template>
