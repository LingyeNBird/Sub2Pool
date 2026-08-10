<script setup lang="ts">
import { computed } from "vue";

import type {
  CostHistoryMaintenancePreview,
  HistoricalUsageMaintenancePreview,
} from "@/types";

const {
  preview,
  costPreview,
  checking,
  backfilling,
  checkingCost,
  repairingCost,
  rebuilding,
} = defineProps<{
  preview: HistoricalUsageMaintenancePreview | null;
  costPreview: CostHistoryMaintenancePreview | null;
  checking: boolean;
  backfilling: boolean;
  checkingCost: boolean;
  repairingCost: boolean;
  rebuilding: boolean;
}>();

const emit = defineEmits<{
  preview: [];
  backfill: [];
  costPreview: [];
  costRepair: [];
  rebuild: [];
}>();

const busy = computed(
  () => checking || backfilling || checkingCost || repairingCost || rebuilding,
);
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
          只读 Sub2API
          请求日志补齐旧版数据，再重放可派生结果。原始百分比、累计成本和观测时间不会改写。
        </p>
      </div>

      <section class="space-y-3">
        <div>
          <h3 class="font-semibold">逐用户用量事实</h3>
          <p class="mt-1 text-xs leading-5 opacity-60">
            为旧观测补齐每个 Sub2API 用户的历史用量。
          </p>
        </div>
        <div v-if="preview" class="grid grid-cols-2 gap-3 text-sm">
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">原始观测</div>
            <div class="mt-1 font-semibold">
              {{ preview.observation_count }}
            </div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">缺失用户事实</div>
            <div class="mt-1 font-semibold">{{ preview.missing_samples }}</div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">可补全事实</div>
            <div class="mt-1 font-semibold">{{ preview.fillable_samples }}</div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">历史请求日志</div>
            <div class="mt-1 font-semibold">
              {{ preview.request_log_count }}
            </div>
          </div>
        </div>
        <div
          v-if="preview?.incompatible_segments"
          class="alert text-sm alert-warning"
        >
          <AppIcon name="exclamation-triangle" class="size-5" />
          <span>
            {{ preview.incompatible_segments }}
            个历史区间的逐用户合计与原始总成本不一致，补全已禁用。
          </span>
        </div>
        <div
          v-else-if="preview && !preview.missing_samples"
          class="alert text-sm alert-success"
        >
          <AppIcon name="check-circle" class="size-5" />
          <span>历史用户用量事实完整，无需补全。</span>
        </div>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-sm" :disabled="busy" @click="emit('preview')">
            <span
              v-if="checking"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="document-magnifying-glass" class="size-4" />
            检查用户缺口
          </button>
          <button
            class="btn btn-primary btn-sm"
            :disabled="!preview?.can_backfill || busy"
            @click="emit('backfill')"
          >
            <span
              v-if="backfilling"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="archive-box" class="size-4" />
            补全用户历史
          </button>
        </div>
      </section>

      <div class="divider my-0"></div>

      <section class="space-y-3">
        <div>
          <h3 class="font-semibold">成本查询区间</h3>
          <p class="mt-1 text-xs leading-5 opacity-60">
            当历史累计快照更换查询起点时，用精确请求日志衔接相邻观测。
          </p>
        </div>
        <div v-if="costPreview" class="grid grid-cols-2 gap-3 text-sm">
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">观测成本区间</div>
            <div class="mt-1 font-semibold">
              {{ costPreview.observation_interval_count }}
            </div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">快照坐标变化</div>
            <div class="mt-1 font-semibold">
              {{ costPreview.coordinate_changes }}
            </div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">历史请求日志</div>
            <div class="mt-1 font-semibold">
              {{ costPreview.request_log_count }}
            </div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="opacity-60">核对冲突</div>
            <div class="mt-1 font-semibold">
              {{ costPreview.snapshot_conflicts }}
            </div>
          </div>
        </div>
        <div
          v-if="costPreview?.snapshot_conflicts"
          class="alert text-sm alert-warning"
        >
          <AppIcon name="exclamation-triangle" class="size-5" />
          <span>
            请求日志与 {{ costPreview.snapshot_conflicts }}
            个同窗口快照增量不一致。为避免覆盖正确区间，重取已禁用。
          </span>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            class="btn btn-sm"
            :disabled="busy"
            @click="emit('costPreview')"
          >
            <span
              v-if="checkingCost"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="document-magnifying-glass" class="size-4" />
            检查成本区间
          </button>
          <button
            class="btn btn-sm btn-warning"
            :disabled="!costPreview?.can_repair || busy"
            @click="emit('costRepair')"
          >
            <span
              v-if="repairingCost"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="arrow-path" class="size-4" />
            重取历史成本
          </button>
        </div>
      </section>

      <div class="divider my-0"></div>

      <button
        class="btn btn-outline btn-sm"
        :disabled="busy"
        @click="emit('rebuild')"
      >
        <span
          v-if="rebuilding"
          class="loading loading-xs loading-spinner"
        ></span>
        <AppIcon v-else name="arrow-path" class="size-4" />
        仅重建全部派生结果
      </button>
    </div>
  </section>
</template>
