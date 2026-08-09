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
      <p
        v-if="data.weekly_quota_model === 'time_varying'"
        class="text-sm leading-6 opacity-70"
      >
        时变模型结合完整成本轨迹、上游整数进度和连续容量路径，估计各参与者本周期已经使用的权益。合同份额只用于计算剩余权益，不参与消费归属。
      </p>
      <p v-else class="text-sm leading-6 opacity-70">
        平均恒定模型按本周期累计成本比例分配上游整数进度，并据此计算各参与者剩余权益。美元余额只是一条人工调整建议。
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
