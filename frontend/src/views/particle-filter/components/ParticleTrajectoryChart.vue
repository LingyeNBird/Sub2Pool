<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import type { ParticleTrajectoryPoint } from "@/types";
import { formatCurrency } from "@/utils/formatters";
import {
  pointTime,
  trajectoryFrame,
  type TrajectoryFrame,
} from "../trajectory";

const props = defineProps<{
  points: ParticleTrajectoryPoint[];
  progress: number;
}>();

interface PlotPoint {
  x: number;
  y: number;
}

interface TimelineSample {
  timeMs: number;
  point: ParticleTrajectoryPoint;
}

const mobile = ref(false);
const dateTime = useDateTime();
let mobileQuery: MediaQueryList | null = null;

const width = computed(() => (mobile.value ? 420 : 1040));
const height = computed(() => (mobile.value ? 760 : 520));
const padding = computed(() =>
  mobile.value
    ? { top: 68, right: 32, bottom: 52, left: 66 }
    : { top: 48, right: 42, bottom: 72, left: 78 },
);
const activeFrame = computed<TrajectoryFrame | null>(() =>
  trajectoryFrame(props.points, props.progress),
);
const pointTimes = computed(() =>
  props.points.map((point, index) => pointTime(point, index)),
);
const firstTime = computed(() => pointTimes.value[0] ?? 0);
const lastTime = computed(
  () => pointTimes.value[pointTimes.value.length - 1] ?? firstTime.value,
);
const activeSamples = computed<TimelineSample[]>(() => {
  const frame = activeFrame.value;
  if (!frame) return [];
  const completed = props.points
    .slice(0, frame.leftIndex + 1)
    .map((point, index) => ({
      timeMs: pointTimes.value[index] ?? index,
      point,
    }));
  if (frame.mix > 0) {
    completed.push({ timeMs: frame.timeMs, point: frame.point });
  }
  return completed;
});
const capacityMinimum = computed(() =>
  Math.min(...props.points.map((point) => point.range_min_usd)),
);
const capacityMaximum = computed(() =>
  Math.max(...props.points.map((point) => point.range_max_usd)),
);
const capacitySpan = computed(() =>
  Math.max(1, capacityMaximum.value - capacityMinimum.value),
);

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function timeRatio(timeMs: number): number {
  const span = lastTime.value - firstTime.value;
  if (span <= 0) return 0.5;
  return clamp((timeMs - firstTime.value) / span, 0, 1);
}

function capacityRatio(value: number): number {
  return (value - capacityMinimum.value) / capacitySpan.value;
}

function coordinate(timeMs: number, capacity: number): PlotPoint {
  const box = padding.value;
  const plotWidth = width.value - box.left - box.right;
  const plotHeight = height.value - box.top - box.bottom;
  if (mobile.value) {
    return {
      x: box.left + capacityRatio(capacity) * plotWidth,
      y: box.top + timeRatio(timeMs) * plotHeight,
    };
  }
  return {
    x: box.left + timeRatio(timeMs) * plotWidth,
    y: box.top + (1 - capacityRatio(capacity)) * plotHeight,
  };
}

function smoothCoordinates(points: PlotPoint[]): string {
  if (!points.length) return "";
  const first = points[0];
  let path = `M ${first.x.toFixed(2)} ${first.y.toFixed(2)}`;
  if (points.length === 1) return path;
  if (points.length === 2) {
    const last = points[1];
    return `${path} L ${last.x.toFixed(2)} ${last.y.toFixed(2)}`;
  }

  for (let index = 0; index < points.length - 1; index += 1) {
    const before = points[Math.max(0, index - 1)];
    const start = points[index];
    const end = points[index + 1];
    const after = points[Math.min(points.length - 1, index + 2)];
    const factor = 0.13;
    const minimumX = Math.min(start.x, end.x);
    const maximumX = Math.max(start.x, end.x);
    const minimumY = Math.min(start.y, end.y);
    const maximumY = Math.max(start.y, end.y);
    const firstControl = {
      x: clamp(start.x + (end.x - before.x) * factor, minimumX, maximumX),
      y: clamp(start.y + (end.y - before.y) * factor, minimumY, maximumY),
    };
    const secondControl = {
      x: clamp(end.x - (after.x - start.x) * factor, minimumX, maximumX),
      y: clamp(end.y - (after.y - start.y) * factor, minimumY, maximumY),
    };
    path +=
      ` C ${firstControl.x.toFixed(2)} ${firstControl.y.toFixed(2)}` +
      ` ${secondControl.x.toFixed(2)} ${secondControl.y.toFixed(2)}` +
      ` ${end.x.toFixed(2)} ${end.y.toFixed(2)}`;
  }
  return path;
}

function linePath(
  samples: TimelineSample[],
  value: (point: ParticleTrajectoryPoint) => number,
): string {
  return smoothCoordinates(
    samples.map((sample) => coordinate(sample.timeMs, value(sample.point))),
  );
}

const estimatePath = computed(() =>
  linePath(activeSamples.value, (point) => point.capacity_usd),
);
const lowerPath = computed(() =>
  linePath(activeSamples.value, (point) => point.capacity_lower_usd),
);
const upperPath = computed(() =>
  linePath(activeSamples.value, (point) => point.capacity_upper_usd),
);
const rangeMinimumPath = computed(() =>
  linePath(activeSamples.value, (point) => point.range_min_usd),
);
const rangeMaximumPath = computed(() =>
  linePath(activeSamples.value, (point) => point.range_max_usd),
);
const credibleBandPath = computed(() => {
  const upper = activeSamples.value.map((sample) =>
    coordinate(sample.timeMs, sample.point.capacity_upper_usd),
  );
  const lower = activeSamples.value
    .map((sample) => coordinate(sample.timeMs, sample.point.capacity_lower_usd))
    .reverse();
  const upperPathValue = smoothCoordinates(upper);
  const lowerPathValue = smoothCoordinates(lower).replace(/^M/, "L");
  return `${upperPathValue} ${lowerPathValue} Z`;
});

const quantileIndices = [7, 15, 23, 31, 39, 47, 55, 63, 71, 79, 87];
const quantilePaths = computed(() =>
  quantileIndices.map((particleIndex) =>
    linePath(
      activeSamples.value,
      (point) => point.particles_usd[particleIndex] ?? point.capacity_usd,
    ),
  ),
);
const activePoint = computed(() => activeFrame.value?.point ?? null);
const activeCoordinate = computed(() =>
  activeFrame.value && activePoint.value
    ? coordinate(activeFrame.value.timeMs, activePoint.value.capacity_usd)
    : { x: 0, y: 0 },
);
const activeParticles = computed(() => {
  const frame = activeFrame.value;
  if (!frame) return [];
  return (frame.point.particles_usd ?? []).map((capacity, index) => {
    const plotted = coordinate(frame.timeMs, capacity);
    const jitter = Math.sin((index + 1) * 12.9898) * 8;
    return {
      x: plotted.x + (mobile.value ? 0 : jitter),
      y: plotted.y + (mobile.value ? jitter : 0),
      delay: `${-((index % 12) * 0.12).toFixed(2)}s`,
    };
  });
});
const capacityTicks = computed(() =>
  Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    const value = capacityMinimum.value + capacitySpan.value * ratio;
    const plotted = mobile.value
      ? coordinate(firstTime.value, value)
      : coordinate(
          firstTime.value,
          capacityMaximum.value - capacitySpan.value * ratio,
        );
    return {
      value: mobile.value
        ? value
        : capacityMaximum.value - capacitySpan.value * ratio,
      x: plotted.x,
      y: plotted.y,
    };
  }),
);
const timeTickIndices = computed(() => {
  if (props.points.length <= 1) return [0];
  return [
    ...new Set([
      0,
      Math.floor((props.points.length - 1) / 2),
      props.points.length - 1,
    ]),
  ];
});

function timeLabel(index: number): string {
  return mobile.value
    ? `第 ${index + 1} 次`
    : dateTime(props.points[index]?.observed_at);
}

function syncMobile(event: MediaQueryList | MediaQueryListEvent) {
  mobile.value = event.matches;
}

onMounted(() => {
  mobileQuery = window.matchMedia("(max-width: 47.999rem)");
  syncMobile(mobileQuery);
  mobileQuery.addEventListener("change", syncMobile);
});

onBeforeUnmount(() => {
  mobileQuery?.removeEventListener("change", syncMobile);
});
</script>

<template>
  <svg
    class="block w-full overflow-hidden [overflow-anchor:none]"
    :viewBox="`0 0 ${width} ${height}`"
    role="img"
    aria-label="粒子滤波容量轨迹、可信区间与后验粒子点云"
  >
    <defs>
      <filter id="particle-glow" x="-100%" y="-100%" width="300%" height="300%">
        <feGaussianBlur stdDeviation="3" result="blur" />
        <feMerge>
          <feMergeNode in="blur" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
      <linearGradient id="credible-fill" x1="0" y1="0" x2="1" y2="1">
        <stop
          offset="0"
          stop-color="var(--color-primary)"
          stop-opacity="0.28"
        />
        <stop
          offset="1"
          stop-color="var(--color-secondary)"
          stop-opacity="0.08"
        />
      </linearGradient>
    </defs>

    <g class="opacity-20">
      <template v-for="tick in capacityTicks" :key="tick.value">
        <line
          v-if="mobile"
          :x1="tick.x"
          :x2="tick.x"
          :y1="padding.top"
          :y2="height - padding.bottom"
          stroke="currentColor"
          stroke-dasharray="3 7"
        />
        <line
          v-else
          :x1="padding.left"
          :x2="width - padding.right"
          :y1="tick.y"
          :y2="tick.y"
          stroke="currentColor"
          stroke-dasharray="3 7"
        />
      </template>
    </g>

    <g class="fill-current text-[11px] opacity-60">
      <text
        v-for="tick in capacityTicks"
        :key="`label-${tick.value}`"
        :x="mobile ? tick.x : padding.left - 12"
        :y="mobile ? padding.top - 22 : tick.y + 4"
        :text-anchor="mobile ? 'middle' : 'end'"
      >
        {{ `$${Math.round(tick.value)}` }}
      </text>
      <text
        v-for="index in timeTickIndices"
        :key="`time-${index}`"
        :x="
          mobile
            ? padding.left - 14
            : coordinate(pointTimes[index] ?? index, capacityMinimum).x
        "
        :y="
          mobile
            ? coordinate(pointTimes[index] ?? index, capacityMinimum).y + 4
            : height - 34
        "
        :text-anchor="
          mobile
            ? 'end'
            : index === 0
              ? 'start'
              : index === points.length - 1
                ? 'end'
                : 'middle'
        "
      >
        {{ timeLabel(index) }}
      </text>
    </g>

    <path :d="credibleBandPath" fill="url(#credible-fill)" />
    <path
      :d="rangeMinimumPath"
      fill="none"
      stroke="var(--color-warning)"
      stroke-width="1.5"
      stroke-dasharray="8 7"
      opacity="0.5"
    />
    <path
      :d="rangeMaximumPath"
      fill="none"
      stroke="var(--color-warning)"
      stroke-width="1.5"
      stroke-dasharray="8 7"
      opacity="0.5"
    />
    <path
      v-for="(path, index) in quantilePaths"
      :key="index"
      :d="path"
      fill="none"
      stroke="var(--color-primary)"
      stroke-width="1"
      opacity="0.12"
    />
    <path
      :d="lowerPath"
      fill="none"
      stroke="var(--color-primary)"
      stroke-width="1.5"
      opacity="0.45"
    />
    <path
      :d="upperPath"
      fill="none"
      stroke="var(--color-primary)"
      stroke-width="1.5"
      opacity="0.45"
    />
    <path
      :d="estimatePath"
      fill="none"
      stroke="var(--color-primary)"
      stroke-linecap="round"
      stroke-linejoin="round"
      stroke-width="4"
      filter="url(#particle-glow)"
    />
    <path
      class="trajectory-flow"
      :d="estimatePath"
      fill="none"
      stroke="white"
      stroke-linecap="round"
      stroke-width="2"
      stroke-dasharray="2 20"
      opacity="0.75"
    />

    <g filter="url(#particle-glow)">
      <circle
        v-for="(particle, index) in activeParticles"
        :key="index"
        class="particle-breathe"
        :cx="particle.x"
        :cy="particle.y"
        :r="index % 7 === 0 ? 3.2 : 2.2"
        fill="var(--color-primary)"
        :style="{ animationDelay: particle.delay }"
      />
    </g>
    <circle
      :cx="activeCoordinate.x"
      :cy="activeCoordinate.y"
      r="7"
      fill="var(--color-base-100)"
      stroke="var(--color-primary)"
      stroke-width="3"
    />
    <text
      v-if="activePoint"
      :x="activeCoordinate.x + (mobile ? 0 : 14)"
      :y="activeCoordinate.y + (mobile ? -18 : -12)"
      :text-anchor="mobile ? 'middle' : 'start'"
      class="fill-current text-xs font-semibold"
    >
      {{ formatCurrency(activePoint.capacity_usd) }}
    </text>
  </svg>
</template>

<style scoped>
.trajectory-flow {
  animation: trajectory-flow 1.8s linear infinite;
}

.particle-breathe {
  animation: particle-breathe 2.4s ease-in-out infinite;
  transform-box: fill-box;
  transform-origin: center;
}

@keyframes trajectory-flow {
  to {
    stroke-dashoffset: -44;
  }
}

@keyframes particle-breathe {
  0%,
  100% {
    opacity: 0.38;
    transform: scale(0.75);
  }
  50% {
    opacity: 0.95;
    transform: scale(1.35);
  }
}

@media (prefers-reduced-motion: reduce) {
  .trajectory-flow,
  .particle-breathe {
    animation: none;
  }
}
</style>
