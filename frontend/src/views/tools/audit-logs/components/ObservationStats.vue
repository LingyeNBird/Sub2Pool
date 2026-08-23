<script setup lang="ts">
import { useDateTime } from "@/composables/useDateTime";
import type { MonitorSchedule } from "@/types/observations";

import type { ObservationSummary } from "../types";

defineProps<{
  summary: ObservationSummary;
  schedule: MonitorSchedule | null;
  remainingLabel: string;
  countdownProgress: number;
}>();

const dateTime = useDateTime();
</script>

<template>
  <section
    class="stats col-span-12 stats-vertical bg-base-200 shadow-xs xl:stats-horizontal"
  >
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">观测记录</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ summary.total }}
          </div>
          <div class="stat-desc">符合当前筛选条件</div>
        </div>
        <AppIcon
          name="document-magnifying-glass"
          class="size-7 shrink-0 opacity-40"
        />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">累计口径有效样本</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ summary.valid_count }}
          </div>
          <div class="stat-desc">本周期累计成本 ÷ 上游已用百分比</div>
        </div>
        <AppIcon name="check-circle" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">被动快照</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ summary.passive_count }}
          </div>
          <div class="stat-desc">未调用 OpenAI 官方额度接口</div>
        </div>
        <AppIcon name="circle-stack" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div v-if="schedule?.monitoring_enabled" class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0 grow">
          <div class="stat-title">下次自动采样（本地探测）</div>
          <div class="stat-value text-lg font-semibold tabular-nums">
            {{ remainingLabel }}
          </div>
          <progress
            class="progress mt-2 w-full progress-primary"
            :value="countdownProgress"
            max="100"
          ></progress>
          <div class="stat-desc">
            {{ dateTime(schedule.next_local_check_at) }} ·
            全局探测全部启用参与者
          </div>
        </div>
        <AppIcon name="clock" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
  </section>
</template>
