<script setup lang="ts">
import { computed } from "vue";

import type { HistoricalRebuildPreview } from "@/types";

const { preview, checking, rebuilding } = defineProps<{
  preview: HistoricalRebuildPreview | null;
  checking: boolean;
  rebuilding: boolean;
}>();

const emit = defineEmits<{
  preview: [];
  rebuild: [];
}>();

const busy = computed(() => checking || rebuilding);
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body gap-5">
      <div>
        <h2 class="card-title">
          <AppIcon name="wrench-screwdriver" class="size-5" />数据维护
        </h2>
        <p class="mt-2 text-sm leading-6 opacity-70">
          重建会只读 Sub2API 请求日志，覆盖历史成本、逐用户用量、FAST
          修正和全部派生结果。上游百分比及其采样边界、管理操作和无法历史查询的余额保持不变。
        </p>
      </div>

      <section class="space-y-3">
        <div>
          <h3 class="font-semibold">历史数据全量重建</h3>
          <p class="mt-1 text-xs leading-5 opacity-60">
            旧数据库中的累计成本不作为核对基准；重建结果以 Sub2API
            请求日志为准。
          </p>
        </div>
        <div v-if="preview" class="grid grid-cols-2 gap-3 text-sm">
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">百分比观测</div>
            <div class="mt-1 font-semibold">
              {{ preview.observation_count }}
            </div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">历史请求日志</div>
            <div class="mt-1 font-semibold">
              {{ preview.request_log_count }}
            </div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">用量时间点</div>
            <div class="mt-1 font-semibold">
              {{ preview.sample_point_count }}
            </div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">Sub2API 用户</div>
            <div class="mt-1 font-semibold">{{ preview.user_count }}</div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">重建用户事实</div>
            <div class="mt-1 font-semibold">
              {{ preview.rebuilt_user_samples }}
            </div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">重建参与者趋势</div>
            <div class="mt-1 font-semibold">
              {{ preview.rebuilt_participant_samples }}
            </div>
          </div>
        </div>
        <div
          v-if="preview?.nonzero_percent_without_cost"
          class="alert text-sm alert-warning"
        >
          <AppIcon name="exclamation-triangle" class="size-5" />
          <span>
            {{ preview.nonzero_percent_without_cost }}
            条非零百分比观测在请求日志中没有成本。系统不会沿用旧成本；请先确认
            Sub2API 是否保留了对应日期的完整日志。
          </span>
        </div>
        <div v-if="preview && !preview.observation_count" class="alert text-sm">
          <AppIcon name="information-circle" class="size-5" />
          <span>尚无百分比观测，不需要重建。</span>
        </div>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-sm" :disabled="busy" @click="emit('preview')">
            <span
              v-if="checking"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="document-magnifying-glass" class="size-4" />
            检查可重建数据
          </button>
          <button
            class="btn btn-sm btn-warning"
            :disabled="!preview?.can_rebuild || busy"
            @click="emit('rebuild')"
          >
            <span
              v-if="rebuilding"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="arrow-path" class="size-4" />
            重建全部历史
          </button>
        </div>
      </section>
    </div>
  </section>
</template>
