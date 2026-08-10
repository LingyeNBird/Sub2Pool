<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { useDateTime } from "@/composables/useDateTime";
import { ApiError, api } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type {
  ParticleRangePromotion,
  ParticleTrajectoryData,
  ParticleTrajectoryPeriod,
  ParticleTrajectoryPoint,
} from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

import ParticleTrajectoryChart from "./components/ParticleTrajectoryChart.vue";
import { trajectoryFrame } from "./trajectory";

const PLAYBACK_DURATION_MS = 12_000;

const data = ref<ParticleTrajectoryData | null>(null);
const loading = ref(true);
const message = ref("");
const playbackProgress = ref(0);
const playing = ref(false);
const playbackSpeed = ref<1 | 2 | 4>(2);
const reducedMotion = ref(false);
const auth = useAuthStore();
const dateTime = useDateTime();
let playbackFrame: number | undefined;
let lastFrameTime: number | null = null;
let motionQuery: MediaQueryList | null = null;

const points = computed<ParticleTrajectoryPoint[]>(
  () => data.value?.points ?? [],
);
const promotions = computed<ParticleRangePromotion[]>(
  () => data.value?.promotions ?? [],
);
const periods = computed<ParticleTrajectoryPeriod[]>(() =>
  [...(data.value?.periods ?? [])].reverse(),
);
const selectedPeriodId = ref<number | null>(null);
const selectedPeriod = computed(
  () =>
    periods.value.find((period) => period.id === selectedPeriodId.value) ??
    null,
);
const replayTitle = computed(() => {
  if (!selectedPeriod.value) return "周期粒子重放";
  return selectedPeriod.value.is_current
    ? "当前周期粒子重放"
    : `第 ${selectedPeriod.value.sequence} 周期粒子重放`;
});
const frame = computed(() =>
  trajectoryFrame(points.value, playbackProgress.value),
);
const activePoint = computed(() => frame.value?.point ?? null);
const activeObservationNumber = computed(() =>
  frame.value ? Math.min(points.value.length, frame.value.rightIndex + 1) : 0,
);
const progress = computed(() => playbackProgress.value * 100);
const sourceLabel = computed(() => {
  const labels: Record<string, string> = {
    scheduled: "定时观测",
    manual: "手动观测",
    exhausted: "额度耗尽触发",
    reset: "重置临近",
  };
  return activePoint.value
    ? (labels[activePoint.value.source] ?? activePoint.value.source)
    : "—";
});

function cancelPlaybackFrame() {
  if (playbackFrame !== undefined) {
    window.cancelAnimationFrame(playbackFrame);
  }
  playbackFrame = undefined;
  lastFrameTime = null;
}

function stopPlayback() {
  playing.value = false;
  cancelPlaybackFrame();
}

function advancePlayback(now: number) {
  if (!playing.value) return;
  if (lastFrameTime === null) lastFrameTime = now;
  const elapsed = Math.min(64, Math.max(0, now - lastFrameTime));
  lastFrameTime = now;
  playbackProgress.value = Math.min(
    1,
    playbackProgress.value +
      (elapsed * playbackSpeed.value) / PLAYBACK_DURATION_MS,
  );
  if (playbackProgress.value >= 1) {
    stopPlayback();
    return;
  }
  playbackFrame = window.requestAnimationFrame(advancePlayback);
}

function startPlayback() {
  cancelPlaybackFrame();
  if (reducedMotion.value || points.value.length <= 1) return;
  playing.value = true;
  playbackFrame = window.requestAnimationFrame(advancePlayback);
}

function togglePlayback() {
  if (playing.value) {
    stopPlayback();
    return;
  }
  if (playbackProgress.value >= 1) playbackProgress.value = 0;
  startPlayback();
}

function restartPlayback() {
  stopPlayback();
  playbackProgress.value = 0;
  startPlayback();
}

function selectProgress(rawValue: number) {
  stopPlayback();
  playbackProgress.value = Math.min(1, Math.max(0, rawValue / 1000));
}

function periodDate(value: string) {
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return "日期未知";
  try {
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: auth.timezone,
      month: "numeric",
      day: "numeric",
    }).formatToParts(instant);
    const month = parts.find((part) => part.type === "month")?.value;
    const day = parts.find((part) => part.type === "day")?.value;
    return month && day ? `${month}月${day}日` : "日期未知";
  } catch {
    const fallback = /^(\d{4})\/(\d{1,2})\/(\d{1,2})/u.exec(dateTime(value));
    return fallback
      ? `${Number(fallback[2])}月${Number(fallback[3])}日`
      : "日期未知";
  }
}

function periodLabel(period: ParticleTrajectoryPeriod) {
  return `第 ${period.sequence} 周期 · ${periodDate(period.started_at)}至${periodDate(period.resets_at)}`;
}

function loadSelectedPeriod() {
  void load(selectedPeriodId.value);
}

async function load(periodId: number | null = selectedPeriodId.value) {
  loading.value = true;
  message.value = "";
  stopPlayback();
  try {
    const query = periodId === null ? "" : `?period=${periodId}`;
    data.value = await api<ParticleTrajectoryData>(
      `particle-trajectory${query}`,
    );
    selectedPeriodId.value = data.value.selected_period_id ?? null;
    if (points.value.length) {
      playbackProgress.value = reducedMotion.value ? 1 : 0;
      startPlayback();
    }
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "加载粒子轨迹失败";
  } finally {
    loading.value = false;
  }
}

function syncReducedMotion(event: MediaQueryList | MediaQueryListEvent) {
  reducedMotion.value = event.matches;
  if (event.matches && points.value.length) {
    stopPlayback();
    playbackProgress.value = 1;
  }
}

onMounted(() => {
  motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  syncReducedMotion(motionQuery);
  motionQuery.addEventListener("change", syncReducedMotion);
  void load();
});

onBeforeUnmount(() => {
  stopPlayback();
  motionQuery?.removeEventListener("change", syncReducedMotion);
});
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">额度管理</RouterLink></li>
          <li><h1>粒子轨迹</h1></li>
        </ul>
      </div>
    </div>
    <label v-if="periods.length" class="form-control w-full gap-1 lg:w-72">
      <span class="text-xs font-medium opacity-60">历史周期</span>
      <select
        v-model.number="selectedPeriodId"
        class="select w-full select-sm"
        :disabled="loading"
        aria-label="选择历史周期"
        @change="loadSelectedPeriod"
      >
        <option v-for="period in periods" :key="period.id" :value="period.id">
          {{ periodLabel(period) }}
        </option>
      </select>
    </label>
    <button class="btn btn-sm" :disabled="loading" @click="loadSelectedPeriod">
      <span v-if="loading" class="loading loading-xs loading-spinner"></span>
      <AppIcon v-else name="arrow-path" class="size-4" />
      重新计算
    </button>
  </PageShellHeader>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>

  <section v-if="loading" class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body flex min-h-96 items-center justify-center">
      <span class="loading loading-lg loading-spinner"></span>
    </div>
  </section>

  <section
    v-else-if="!data?.available"
    class="card col-span-12 bg-base-200 shadow-xs"
  >
    <div class="card-body items-center py-20 text-center">
      <AppIcon name="sparkles" class="size-10 opacity-30" />
      <h2 class="mt-2 card-title">暂时没有粒子轨迹</h2>
      <p class="opacity-60">{{ data?.message }}</p>
    </div>
  </section>

  <template v-else-if="activePoint && data?.segment">
    <section
      class="card col-span-12 bg-base-200 shadow-xs [overflow-anchor:none]"
    >
      <div class="card-body gap-5 p-4 sm:p-6">
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 class="card-title">
              <AppIcon name="sparkles" class="size-5" />
              {{ replayTitle }}
              <span
                class="responsive-help-tooltip tooltip tooltip-bottom"
                data-tip="细线连接相同后验分位位置，并不表示某个粒子的永久身份；点云和轨迹只读，不会写回正式计算。"
              >
                <button
                  type="button"
                  class="btn btn-circle cursor-help btn-ghost btn-xs"
                  aria-label="查看粒子轨迹说明"
                >
                  ?
                </button>
              </span>
            </h2>
            <div class="mt-1 text-sm opacity-60">
              {{ data.segment.reason_label }} ·
              {{ data.particle_count }} 个计算粒子 · 展示
              {{ data.representative_particle_count }} 个等权代表点
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button class="btn btn-sm" @click="restartPlayback">
              <AppIcon name="arrow-path-rounded-square" class="size-4" />
              重放
            </button>
            <button class="btn btn-primary btn-sm" @click="togglePlayback">
              <span v-if="playing" class="inline-flex gap-1" aria-hidden="true">
                <span class="h-3 w-1 rounded bg-current"></span>
                <span class="h-3 w-1 rounded bg-current"></span>
              </span>
              <AppIcon v-else name="play" class="size-4" />
              {{ playing ? "暂停" : "播放" }}
            </button>
            <select
              v-model.number="playbackSpeed"
              class="select w-20 select-sm"
              aria-label="重放速度"
            >
              <option :value="1">1×</option>
              <option :value="2">2×</option>
              <option :value="4">4×</option>
            </select>
          </div>
        </div>

        <div class="stats stats-vertical bg-base-100 xl:stats-horizontal">
          <div class="stat">
            <div class="stat-title">当前容量结论</div>
            <div class="stat-value text-2xl">
              {{ formatCurrency(activePoint.capacity_usd) }}
            </div>
            <div class="stat-desc">
              {{ data.credible_mass_percent }}% 区间
              {{ formatCurrency(activePoint.capacity_lower_usd) }} ~
              {{ formatCurrency(activePoint.capacity_upper_usd) }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-title">当前搜索范围</div>
            <div class="stat-value text-2xl">
              {{ formatCurrency(activePoint.range_min_usd) }} ~
              {{ formatCurrency(activePoint.range_max_usd) }}
            </div>
            <div class="stat-desc">
              {{
                activePoint.range_stage
                  ? `第 ${activePoint.range_stage} 级扩张`
                  : "标准范围"
              }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-title">有效粒子比例</div>
            <div class="stat-value text-2xl">
              {{ formatPercent(activePoint.ess_fraction * 100) }}
            </div>
            <div class="stat-desc">
              {{ activePoint.resampled ? "该步已重采样" : "该步无需重采样" }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-title">观测进度</div>
            <div class="stat-value text-2xl">
              {{ activeObservationNumber }} / {{ points.length }}
            </div>
            <div class="stat-desc">
              {{ sourceLabel }} · {{ dateTime(activePoint.observed_at) }}
            </div>
          </div>
        </div>

        <div
          class="rounded-box border border-base-300 bg-base-100 p-2 [overflow-anchor:none] sm:p-4"
        >
          <ParticleTrajectoryChart
            :points="points"
            :progress="playbackProgress"
          />
        </div>

        <div
          class="flex items-center gap-3 [overflow-anchor:none]"
          @pointerdown="stopPlayback"
        >
          <span class="hidden text-xs opacity-50 sm:inline">起点</span>
          <input
            :value="playbackProgress * 1000"
            type="range"
            class="range grow [touch-action:none] range-primary [overflow-anchor:none] range-xs"
            min="0"
            max="1000"
            step="1"
            aria-label="选择重放进度"
            @input="
              selectProgress(Number(($event.target as HTMLInputElement).value))
            "
          />
          <span class="hidden text-xs opacity-50 sm:inline">当前</span>
          <span class="text-xs tabular-nums opacity-60"
            >{{ progress.toFixed(0) }}%</span
          >
        </div>

        <div class="flex flex-wrap gap-x-5 gap-y-2 text-xs opacity-65">
          <span class="inline-flex items-center gap-2">
            <i class="h-1 w-7 rounded bg-primary"></i>容量中位路径
          </span>
          <span class="inline-flex items-center gap-2">
            <i class="h-3 w-7 rounded bg-primary/20"></i
            >{{ data.credible_mass_percent }}% 可信区间
          </span>
          <span class="inline-flex items-center gap-2">
            <i class="h-px w-7 border-t border-dashed border-warning"></i
            >搜索上下限
          </span>
          <span class="inline-flex items-center gap-2">
            <i
              class="size-2 rounded-full bg-primary shadow-sm shadow-primary"
            ></i
            >后验粒子点云
          </span>
        </div>
      </div>
    </section>

    <section class="card col-span-12 bg-base-200 shadow-xs lg:col-span-7">
      <div class="card-body">
        <h2 class="card-title text-base">
          <AppIcon name="signal" class="size-5" />当前观测诊断
        </h2>
        <div class="grid gap-3 sm:grid-cols-2">
          <div class="rounded-box bg-base-100 p-4">
            <div class="text-xs opacity-55">整数显示 / 潜在进度</div>
            <div class="mt-1 font-semibold">
              {{ formatPercent(activePoint.displayed_percent) }} /
              {{ formatPercent(activePoint.estimated_percent) }}
            </div>
            <div class="mt-1 text-xs opacity-55">
              潜在区间
              {{ formatPercent(activePoint.estimated_percent_lower) }} ~
              {{ formatPercent(activePoint.estimated_percent_upper) }}
            </div>
          </div>
          <div class="rounded-box bg-base-100 p-4">
            <div class="text-xs opacity-55">边界粒子质量</div>
            <div class="mt-1 font-semibold">
              下界 {{ formatPercent(activePoint.boundary_mass.lower * 100) }} ·
              上界 {{ formatPercent(activePoint.boundary_mass.upper * 100) }}
            </div>
            <div class="mt-1 text-xs opacity-55">
              双证据成立后才会扩大搜索范围
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="card col-span-12 bg-base-200 shadow-xs lg:col-span-5">
      <div class="card-body">
        <h2 class="card-title text-base">
          <AppIcon name="arrows-right-left" class="size-5" />范围扩张记录
        </h2>
        <div v-if="promotions.length" class="space-y-3">
          <div
            v-for="promotion in promotions"
            :key="`${promotion.stage}-${promotion.occurred_at}`"
            class="rounded-box bg-base-100 p-4 text-sm"
          >
            <div class="flex items-center justify-between gap-3">
              <strong
                >第 {{ promotion.stage }} 级{{
                  promotion.direction === "upper" ? "向上" : "向下"
                }}扩张</strong
              >
              <span class="badge badge-sm badge-warning">双证据</span>
            </div>
            <div class="mt-2 opacity-65">
              {{ formatCurrency(promotion.from_range_usd[0]) }} ~
              {{ formatCurrency(promotion.from_range_usd[1]) }}
              →
              {{ formatCurrency(promotion.to_range_usd[0]) }} ~
              {{ formatCurrency(promotion.to_range_usd[1]) }}
            </div>
            <div class="mt-1 text-xs opacity-50">
              {{ dateTime(promotion.occurred_at) }}
            </div>
          </div>
        </div>
        <div v-else class="py-8 text-center text-sm opacity-55">
          该周期未触发范围扩张
        </div>
      </div>
    </section>
  </template>
</template>
