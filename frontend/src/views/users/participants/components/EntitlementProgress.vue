<script setup lang="ts">
import { computed } from "vue";

import { formatCompactPercent } from "@/utils/formatters";

const props = defineProps<{
  usagePercent: number | null | undefined;
  progressLabel: string;
}>();

const complete = computed(
  () => props.usagePercent != null && Number.isFinite(props.usagePercent),
);
const consumedPercent = computed(() => Math.max(0, props.usagePercent ?? 0));
const overusedPercent = computed(() =>
  Math.max(0, consumedPercent.value - 100),
);
const consumedWidth = computed(() => Math.min(100, consumedPercent.value));
const remainingPercent = computed(() =>
  Math.max(0, 100 - consumedPercent.value),
);
</script>

<template>
  <div
    class="relative flex h-7 min-w-0 overflow-hidden rounded-box bg-base-300 text-xs font-semibold tabular-nums"
    role="img"
    :aria-label="
      complete
        ? `${progressLabel}：已使用 ${formatCompactPercent(consumedPercent)}，剩余 ${formatCompactPercent(remainingPercent)}`
        : `${progressLabel}：等待完整测算`
    "
  >
    <template v-if="complete">
      <div class="flex h-full w-full" aria-hidden="true">
        <div
          v-if="consumedWidth > 0"
          class="h-full shrink-0"
          :class="overusedPercent > 0 ? 'bg-error' : 'bg-warning'"
          :style="{ width: `${consumedWidth}%` }"
        ></div>
        <div
          v-if="remainingPercent > 0"
          class="h-full shrink-0 bg-primary"
          :style="{ width: `${remainingPercent}%` }"
        ></div>
      </div>
      <span
        v-if="overusedPercent > 0"
        class="pointer-events-none absolute inset-0 z-10 flex items-center justify-center px-2 text-[10px] whitespace-nowrap text-error-content sm:text-xs"
      >
        已使用 {{ formatCompactPercent(consumedPercent) }} · 超出
        {{ formatCompactPercent(overusedPercent) }}
      </span>
      <template v-else>
        <span
          v-if="consumedWidth > 0"
          class="pointer-events-none absolute inset-y-0 left-0 z-10 flex max-w-full min-w-fit items-center justify-center px-1 text-[10px] whitespace-nowrap text-white [text-shadow:0_1px_2px_rgb(0_0_0/0.8)] sm:text-xs"
          :style="{ width: `${consumedWidth}%` }"
        >
          已使用 {{ formatCompactPercent(consumedPercent) }}
        </span>
        <span
          v-if="remainingPercent > 0"
          class="pointer-events-none absolute inset-y-0 right-0 z-10 flex max-w-full min-w-fit items-center justify-center px-1 text-[10px] whitespace-nowrap text-white [text-shadow:0_1px_2px_rgb(0_0_0/0.8)] sm:text-xs"
          :style="{ width: `${remainingPercent}%` }"
        >
          剩余 {{ formatCompactPercent(remainingPercent) }}
        </span>
      </template>
    </template>
    <div
      v-else
      class="absolute inset-0 flex items-center justify-center text-xs opacity-60"
    >
      等待完整测算
    </div>
  </div>
</template>
