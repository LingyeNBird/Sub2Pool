<script setup lang="ts">
import { computed } from "vue";

import type { MonitoredAccount } from "@/types/accounts";

const props = defineProps<{ account: MonitoredAccount }>();

const profileCapacityDefaults: Record<
  MonitoredAccount["effective_quota_profile"],
  {
    min: number;
    max: number;
    sliderMin: number;
    sliderMax: number;
    step: number;
  }
> = {
  plus: { min: 100, max: 200, sliderMin: 10, sliderMax: 1000, step: 10 },
  pro_5x: { min: 500, max: 1500, sliderMin: 100, sliderMax: 3000, step: 50 },
  pro_20x: { min: 1400, max: 4000, sliderMin: 500, sliderMax: 6000, step: 100 },
};

const displayedProfile = computed(() =>
  props.account.quota_profile !== "auto"
    ? props.account.quota_profile
    : props.account.detected_plan_type === "plus"
      ? "plus"
      : "pro_20x",
);
const defaults = computed(
  () => profileCapacityDefaults[displayedProfile.value],
);
const capacityMin = computed(
  () => props.account.capacity_min_usd_override ?? defaults.value.min,
);
const capacityMax = computed(
  () => props.account.capacity_max_usd_override ?? defaults.value.max,
);
const domain = computed(() => {
  const min = Math.min(
    defaults.value.sliderMin,
    Math.floor(capacityMin.value / defaults.value.step) * defaults.value.step,
  );
  const max = Math.max(
    defaults.value.sliderMax,
    Math.ceil(capacityMax.value / defaults.value.step) * defaults.value.step,
  );
  const gap = capacityMax.value - capacityMin.value;
  const coarseStep = Math.min(
    defaults.value.step,
    Math.max(0.01, Math.floor((gap / 2) * 100) / 100),
  );
  const aligned = [capacityMin.value, capacityMax.value].every((value) => {
    const offset = (value - min) / coarseStep;
    return Math.abs(offset - Math.round(offset)) < 1e-8;
  });
  return { min, max, step: aligned ? coarseStep : 0.01 };
});
const rangeStyle = computed(() => {
  const span = domain.value.max - domain.value.min;
  return {
    "--range-start": `${((capacityMin.value - domain.value.min) / span) * 100}%`,
    "--range-end": `${((capacityMax.value - domain.value.min) / span) * 100}%`,
  };
});

function updateBoundary(
  boundary: "min" | "max",
  event: Event,
  minimumGap = 0.01,
) {
  const parsed = Number((event.target as HTMLInputElement).value);
  if (!Number.isFinite(parsed)) return;
  const value = Math.round(parsed * 100) / 100;
  props.account.capacity_min_usd_override =
    boundary === "min"
      ? Math.max(1, Math.min(value, capacityMax.value - minimumGap))
      : capacityMin.value;
  props.account.capacity_max_usd_override =
    boundary === "max"
      ? Math.min(50000, Math.max(value, capacityMin.value + minimumGap))
      : capacityMax.value;
}

function reset() {
  props.account.capacity_min_usd_override = null;
  props.account.capacity_max_usd_override = null;
}

function formatCapacity(value: number) {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

defineExpose({ reset });
</script>

<template>
  <div class="rounded-box border border-base-300 bg-base-200/45 px-4 py-3">
    <div class="flex flex-wrap items-start justify-between gap-2">
      <div>
        <div class="text-sm font-medium">基础容量范围</div>
        <p class="mt-0.5 text-xs leading-5 opacity-60">
          粒子滤波每个新周期从这里开始；越界证据仍可触发上下扩张。
        </p>
      </div>
      <button
        v-if="account.capacity_min_usd_override != null"
        type="button"
        class="btn btn-ghost btn-xs"
        @click="reset"
      >
        恢复档位默认值
      </button>
    </div>
    <div class="capacity-range mt-3" :style="rangeStyle">
      <div class="capacity-range__track">
        <span class="capacity-range__active"></span>
      </div>
      <input
        type="range"
        aria-label="基础容量下限"
        :min="domain.min"
        :max="domain.max"
        :step="domain.step"
        :value="capacityMin"
        @input="updateBoundary('min', $event, domain.step)"
      />
      <input
        type="range"
        aria-label="基础容量上限"
        :min="domain.min"
        :max="domain.max"
        :step="domain.step"
        :value="capacityMax"
        @input="updateBoundary('max', $event, domain.step)"
      />
    </div>
    <div class="mt-2 grid grid-cols-2 gap-3">
      <fieldset class="fieldset min-w-0">
        <label class="label text-xs">下限（美元）</label>
        <label class="input w-full input-sm">
          <span class="opacity-50">$</span>
          <input
            type="number"
            aria-label="基础容量下限（美元）"
            min="1"
            max="49999.99"
            step="0.01"
            :value="capacityMin"
            @change="updateBoundary('min', $event)"
          />
        </label>
      </fieldset>
      <fieldset class="fieldset min-w-0">
        <label class="label text-xs">上限（美元）</label>
        <label class="input w-full input-sm">
          <span class="opacity-50">$</span>
          <input
            type="number"
            aria-label="基础容量上限（美元）"
            min="1.01"
            max="50000"
            step="0.01"
            :value="capacityMax"
            @change="updateBoundary('max', $event)"
          />
        </label>
      </fieldset>
    </div>
    <p class="mt-1 text-xs opacity-60">
      当前基础范围：${{ formatCapacity(capacityMin) }} – ${{
        formatCapacity(capacityMax)
      }}
      <span v-if="account.capacity_min_usd_override != null"> · 自定义 </span>
    </p>
  </div>
</template>

<style scoped>
.capacity-range {
  position: relative;
  height: 1.75rem;
}

.capacity-range__track {
  position: absolute;
  top: 50%;
  right: 0.5rem;
  left: 0.5rem;
  height: 0.3rem;
  overflow: hidden;
  border-radius: 999px;
  background: var(--color-base-300);
  transform: translateY(-50%);
}

.capacity-range__active {
  position: absolute;
  inset-block: 0;
  right: calc(100% - var(--range-end));
  left: var(--range-start);
  border-radius: inherit;
  background: var(--color-primary);
}

.capacity-range input[type="range"] {
  position: absolute;
  width: 100%;
  height: 1.75rem;
  appearance: none;
  background: transparent;
  pointer-events: none;
}

.capacity-range input[type="range"]::-webkit-slider-thumb {
  width: 1.25rem;
  height: 1.25rem;
  appearance: none;
  border: 2px solid var(--color-primary);
  border-radius: 999px;
  background: var(--color-base-100);
  box-shadow: 0 1px 3px
    color-mix(in oklab, var(--color-base-content) 25%, transparent);
  cursor: grab;
  pointer-events: auto;
}

.capacity-range input[type="range"]::-moz-range-thumb {
  width: 1.25rem;
  height: 1.25rem;
  border: 2px solid var(--color-primary);
  border-radius: 999px;
  background: var(--color-base-100);
  box-shadow: 0 1px 3px
    color-mix(in oklab, var(--color-base-content) 25%, transparent);
  cursor: grab;
  pointer-events: auto;
}
</style>
