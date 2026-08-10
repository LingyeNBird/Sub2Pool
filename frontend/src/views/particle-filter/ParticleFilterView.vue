<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { useDateTime } from "@/composables/useDateTime";
import { ApiError, api } from "@/services/api";
import type {
  ParticleRangePromotion,
  ParticleTrajectoryData,
  ParticleTrajectoryPoint,
} from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

import ParticleTrajectoryChart from "./components/ParticleTrajectoryChart.vue";

const data = ref<ParticleTrajectoryData | null>(null);
const loading = ref(true);
const message = ref("");
const activeIndex = ref(0);
const playing = ref(false);
const playbackSpeed = ref<1 | 2 | 4>(2);
const reducedMotion = ref(false);
const dateTime = useDateTime();
let playbackTimer: number | undefined;
let motionQuery: MediaQueryList | null = null;

const points = computed<ParticleTrajectoryPoint[]>(
  () => data.value?.points ?? [],
);
const promotions = computed<ParticleRangePromotion[]>(
  () => data.value?.promotions ?? [],
);
const activePoint = computed(() => points.value[activeIndex.value] ?? null);
const progress = computed(() =>
  points.value.length <= 1
    ? 100
    : (activeIndex.value / (points.value.length - 1)) * 100,
);
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

function clearPlaybackTimer() {
  window.clearTimeout(playbackTimer);
  playbackTimer = undefined;
}

function schedulePlayback() {
  clearPlaybackTimer();
  if (!playing.value || points.value.length <= 1) return;
  playbackTimer = window.setTimeout(() => {
    if (activeIndex.value >= points.value.length - 1) {
      playing.value = false;
      return;
    }
    activeIndex.value += 1;
    schedulePlayback();
  }, 900 / playbackSpeed.value);
}

function togglePlayback() {
  if (playing.value) {
    playing.value = false;
    return;
  }
  if (activeIndex.value >= points.value.length - 1) activeIndex.value = 0;
  playing.value = true;
}

function restartPlayback() {
  activeIndex.value = 0;
  playing.value = !reducedMotion.value && points.value.length > 1;
}

function selectPoint(index: number) {
  activeIndex.value = Math.max(0, Math.min(index, points.value.length - 1));
  playing.value = false;
}

async function load() {
  loading.value = true;
  message.value = "";
  playing.value = false;
  clearPlaybackTimer();
  try {
    data.value = await api<ParticleTrajectoryData>("particle-trajectory");
    if (points.value.length) {
      activeIndex.value = reducedMotion.value ? points.value.length - 1 : 0;
      playing.value = !reducedMotion.value && points.value.length > 1;
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
    activeIndex.value = points.value.length - 1;
    playing.value = false;
  }
}

watch([playing, playbackSpeed], schedulePlayback);

onMounted(() => {
  motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  syncReducedMotion(motionQuery);
  motionQuery.addEventListener("change", syncReducedMotion);
  void load();
});

onBeforeUnmount(() => {
  clearPlaybackTimer();
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
    <button class="btn btn-sm" :disabled="loading" @click="load">
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
    <section class="card col-span-12 bg-base-200 shadow-xs">
      <div class="card-body gap-5 p-4 sm:p-6">
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 class="card-title">
              <AppIcon name="sparkles" class="size-5" />
              当前周期粒子重放
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
              {{ activeIndex + 1 }} / {{ points.length }}
            </div>
            <div class="stat-desc">
              {{ sourceLabel }} · {{ dateTime(activePoint.observed_at) }}
            </div>
          </div>
        </div>

        <div class="rounded-box border border-base-300 bg-base-100 p-2 sm:p-4">
          <ParticleTrajectoryChart
            :points="points"
            :active-index="activeIndex"
            @select="selectPoint"
          />
        </div>

        <div class="flex items-center gap-3">
          <span class="hidden text-xs opacity-50 sm:inline">起点</span>
          <input
            :value="activeIndex"
            type="range"
            class="range grow range-primary range-xs"
            min="0"
            :max="Math.max(0, points.length - 1)"
            step="1"
            aria-label="选择观测点"
            @input="
              selectPoint(Number(($event.target as HTMLInputElement).value))
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
          当前周期未触发范围扩张
        </div>
      </div>
    </section>
  </template>
</template>
