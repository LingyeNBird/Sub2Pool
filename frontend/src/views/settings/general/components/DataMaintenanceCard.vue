<script setup lang="ts">
import type { HistoricalUsageMaintenancePreview } from "@/types";

const { preview, checking, backfilling, rebuilding } = defineProps<{
  preview: HistoricalUsageMaintenancePreview | null;
  checking: boolean;
  backfilling: boolean;
  rebuilding: boolean;
}>();

const emit = defineEmits<{
  preview: [];
  backfill: [];
  rebuild: [];
}>();
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body gap-4">
      <div>
        <h2 class="card-title">
          <AppIcon name="wrench-screwdriver" class="size-5" />数据维护
        </h2>
        <p class="mt-2 text-sm leading-6 opacity-70">
          旧版本缺少逐用户事实时，可只读 Sub2API
          请求日志完成补全。补全后自动从第一条观测重建；原始百分比、成本和观测时间不会改写。
        </p>
      </div>

      <div v-if="preview" class="grid grid-cols-2 gap-3 text-sm">
        <div class="rounded-box bg-base-100 p-3">
          <div class="opacity-60">原始观测</div>
          <div class="mt-1 font-semibold">{{ preview.observation_count }}</div>
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
          <div class="mt-1 font-semibold">{{ preview.request_log_count }}</div>
        </div>
      </div>

      <div
        v-if="preview?.incompatible_segments"
        class="alert text-sm alert-warning"
      >
        <AppIcon name="exclamation-triangle" class="size-5" />
        <span>
          {{ preview.incompatible_segments }}
          个历史区间的逐用户合计与原始总成本不一致。为避免写入错误事实，补全已禁用。
        </span>
      </div>
      <div
        v-else-if="preview && !preview.missing_samples"
        class="alert text-sm alert-success"
      >
        <AppIcon name="check-circle" class="size-5" />
        <span>当前历史用户用量事实完整，无需补全。</span>
      </div>

      <div class="flex flex-wrap gap-2">
        <button
          class="btn btn-sm"
          :disabled="checking || backfilling || rebuilding"
          @click="emit('preview')"
        >
          <span
            v-if="checking"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="document-magnifying-glass" class="size-4" />
          检查历史缺口
        </button>
        <button
          class="btn btn-primary btn-sm"
          :disabled="!preview?.can_backfill || backfilling || rebuilding"
          @click="emit('backfill')"
        >
          <span
            v-if="backfilling"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="archive-box" class="size-4" />
          补全全部历史
        </button>
        <button
          class="btn btn-outline btn-sm"
          :disabled="rebuilding || backfilling || checking"
          @click="emit('rebuild')"
        >
          <span
            v-if="rebuilding"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="arrow-path" class="size-4" />
          重建全部结果
        </button>
      </div>
    </div>
  </section>
</template>
