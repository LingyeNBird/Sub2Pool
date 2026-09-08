<script setup lang="ts">
import { computed } from "vue";
import { formatCurrency } from "@/utils/formatters";
const props = defineProps<{
  label: string;
  percentBasis: string;
  capacity: number | null;
  segments: {
    label: string;
    value: number | null;
    color: string;
    forecast?: boolean;
  }[];
}>();
const visibleSegments = computed(() =>
  props.segments.filter(
    (s) => !["未归属", "其他历史成员"].includes(s.label) || s.value !== 0,
  ),
);
const total = computed(() =>
  props.segments.reduce((sum, s) => sum + Math.max(0, s.value ?? 0), 0),
);
const denominator = computed(() => props.capacity ?? total.value);
const shareLabel = (value: number | null) =>
  props.capacity != null && props.capacity > 0 && value != null
    ? `占${props.percentBasis} ${((value / props.capacity) * 100).toFixed(1)}%`
    : "额度占比待估算";
</script>
<template>
  <div class="space-y-3">
    <div class="flex flex-wrap justify-between gap-2 text-sm">
      <span>{{ label }}</span>
      <span
        v-if="capacity != null && total > capacity"
        class="badge badge-sm badge-warning"
        >超过整车额度 {{ ((total / capacity - 1) * 100).toFixed(1) }}%</span
      >
      <span v-if="capacity == null" class="text-base-content/60"
        >仅已采集金额分布 · 额度数据不足</span
      >
    </div>
    <div
      class="overflow-x-auto rounded-box border border-base-300"
      role="img"
      :aria-label="label + '，具体金额见下方图例'"
    >
      <div class="flex h-7 min-w-full bg-base-300/40">
        <div
          v-for="(segment, i) in visibleSegments"
          :key="i"
          class="h-7 shrink-0 border-r border-base-100/50"
          :style="{
            width: `${denominator > 0 ? (Math.max(0, segment.value ?? 0) / denominator) * 100 : 0}%`,
            backgroundColor: segment.color,
            opacity: segment.forecast ? 0.3 : 1,
          }"
          :title="`${segment.label} ${formatCurrency(segment.value)} · ${shareLabel(segment.value)}`"
        />
      </div>
    </div>
    <ul class="flex flex-wrap gap-x-5 gap-y-2 text-sm">
      <li
        v-for="(segment, i) in visibleSegments"
        :key="i"
        class="flex items-center gap-2"
      >
        <span
          class="inline-block size-2.5 shrink-0 rounded-full"
          :style="{
            backgroundColor: segment.color,
            opacity: segment.forecast ? 0.3 : 1,
          }"
        />
        <span
          >{{ segment.label }}
          <strong>{{
            segment.value == null ? "未知" : formatCurrency(segment.value)
          }}</strong
          ><span class="text-base-content/60">
            · {{ shareLabel(segment.value) }}</span
          ></span
        >
      </li>
    </ul>
  </div>
</template>
