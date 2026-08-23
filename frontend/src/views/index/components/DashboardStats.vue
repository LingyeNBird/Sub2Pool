<script setup lang="ts">
import { useDateTime } from "@/composables/useDateTime";
import type { DashboardData } from "@/types/dashboard";
import { formatCurrency, formatPercent } from "@/utils/formatters";

defineProps<{
  data: DashboardData;
}>();

defineEmits<{
  showRateBasis: [];
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
          <div class="stat-title">上游周限已用</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ formatPercent(data.cycle?.upstream_used_percent) }}
          </div>
        </div>
        <AppIcon name="gauge" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">
            {{
              data.weekly_quota_model === "constant_average"
                ? "平均美元 / 1%"
                : "模型美元 / 1%"
            }}
          </div>
          <div class="flex items-baseline gap-2">
            <div class="stat-value text-xl font-semibold tabular-nums">
              {{ formatCurrency(data.cycle?.effective_usd_per_percent) }}
            </div>
            <button
              v-if="data.cycle?.rate_calculated"
              type="button"
              class="cursor-pointer text-xs underline underline-offset-2 opacity-50"
              @click="$emit('showRateBasis')"
            >
              查看依据
            </button>
          </div>
        </div>
        <AppIcon name="banknotes" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">需要手动调整</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ data.needs_manual_update_count }}
          </div>
        </div>
        <AppIcon
          name="clipboard-document-check"
          class="size-7 shrink-0 opacity-40"
        />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">上游重置时间</div>
          <div class="stat-value text-lg font-semibold tabular-nums">
            {{ dateTime(data.cycle?.resets_at) }}
          </div>
        </div>
        <AppIcon name="calendar-days" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
  </section>
</template>
