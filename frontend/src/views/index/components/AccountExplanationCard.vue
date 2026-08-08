<script setup lang="ts">
import type { DashboardData } from "@/types";
import { formatPercent } from "@/utils/formatters";

defineProps<{
  data: DashboardData;
}>();
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
    <div class="card-body">
      <h2 class="card-title">
        <AppIcon name="information-circle" class="size-5" />账本说明
      </h2>
      <p class="text-sm leading-6 opacity-70">
        每次有效观测把上游百分比增量按参与者同期美元用量占账号总用量的比例归属。美元限额只是一条手动调整建议；
        百分比权益账本才是最终依据，因此参与者可以在任意时间集中使用自己的全部权益。
      </p>
      <div class="divider my-1"></div>
      <p class="text-sm">
        未归属的已用周限：<strong>{{
          formatPercent(data.cycle?.unattributed_used_percent)
        }}</strong>
      </p>
      <p v-if="data.last_error" class="text-sm text-error">
        {{ data.last_error }}
      </p>
    </div>
  </section>
</template>
