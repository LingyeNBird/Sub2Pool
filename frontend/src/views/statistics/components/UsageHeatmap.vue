<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";

import { formatCurrency } from "@/utils/formatters";

interface HeatmapPoint {
  label: string;
  value: number;
}

const props = defineProps<{
  points: HeatmapPoint[];
  precision: "raw" | "hour" | "day";
  sampleIntervalMinutes: number | null | undefined;
}>();

const viewport = ref<HTMLDivElement | null>(null);
const hoveredPoint = ref<HeatmapPoint | null>(null);
let resizeObserver: ResizeObserver | null = null;

const intervalLabel = computed(
  () =>
    ({
      raw: `相邻两次探测（通常约 ${props.sampleIntervalMinutes ?? "—"} 分钟）`,
      hour: "1 小时",
      day: "1 天",
    })[props.precision],
);

const maximum = computed(() =>
  Math.max(0, ...props.points.map((point) => point.value)),
);

function intensity(value: number) {
  if (value <= 0 || maximum.value <= 0) return 0;
  return Math.min(4, Math.max(1, Math.ceil((value / maximum.value) * 4)));
}

function cellClass(value: number) {
  return [
    "size-3.5 rounded-[0.2rem] border border-base-content/5",
    [
      "bg-base-300",
      "bg-primary/20",
      "bg-primary/40",
      "bg-primary/65",
      "bg-primary",
    ][intensity(value)],
  ];
}

async function scrollToLatest() {
  await nextTick();
  if (viewport.value) {
    viewport.value.scrollLeft = viewport.value.scrollWidth;
  }
}

watch(() => [props.points.length, props.precision], scrollToLatest);

onMounted(() => {
  scrollToLatest();
  if (viewport.value) {
    resizeObserver = new ResizeObserver(scrollToLatest);
    resizeObserver.observe(viewport.value);
  }
});

onBeforeUnmount(() => resizeObserver?.disconnect());
</script>

<template>
  <div
    ref="viewport"
    class="h-full overflow-x-auto overflow-y-hidden"
    role="img"
    :aria-label="`用主题色深浅表示用量的热力图，每格代表${intervalLabel}，最大格为${formatCurrency(maximum)}`"
    @mouseleave="hoveredPoint = null"
  >
    <div class="flex h-full w-max min-w-full flex-col justify-center px-1">
      <div class="mb-2 flex min-h-5 items-center justify-between gap-4 text-xs">
        <span class="opacity-60">每格：{{ intervalLabel }}</span>
        <span class="tabular-nums">
          <template v-if="hoveredPoint">
            {{ hoveredPoint.label }} · {{ formatCurrency(hoveredPoint.value) }}
          </template>
        </span>
      </div>
      <div
        class="grid h-32 w-max min-w-full auto-cols-[0.875rem] grid-flow-col grid-rows-7 justify-end gap-1"
      >
        <span
          v-for="(point, index) in points"
          :key="`${point.label}-${index}`"
          :class="cellClass(point.value)"
          :aria-label="`${point.label}，新增用量 ${formatCurrency(point.value)}`"
          @mouseenter="hoveredPoint = point"
        ></span>
      </div>
      <div class="mt-2 flex min-h-4 items-center justify-end gap-1 text-xs">
        <span class="mr-1 opacity-50">少</span>
        <span class="size-3 rounded-[0.2rem] bg-base-300"></span>
        <span class="size-3 rounded-[0.2rem] bg-primary/20"></span>
        <span class="size-3 rounded-[0.2rem] bg-primary/40"></span>
        <span class="size-3 rounded-[0.2rem] bg-primary/65"></span>
        <span class="size-3 rounded-[0.2rem] bg-primary"></span>
        <span class="ml-1 opacity-50">多</span>
      </div>
    </div>
  </div>
</template>
