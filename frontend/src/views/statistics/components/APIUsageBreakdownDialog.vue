<script setup lang="ts">
import CorrectionAmount from "@/components/common/CorrectionAmount.vue";
import { computed, ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import { ApiError, api } from "@/services/api";
import type { APIUsageBreakdown } from "@/types/statistics";
import { formatCurrency, formatPercent } from "@/utils/formatters";

import APIUsageDonut from "./APIUsageDonut.vue";

const dialog = ref<HTMLDialogElement | null>(null);
const data = ref<APIUsageBreakdown | null>(null);
const participantName = ref("");
const loading = ref(false);
const message = ref("");
const requestSequence = ref(0);
const dateTime = useDateTime();

const usedKeys = computed(
  () => data.value?.api_keys.filter((item) => item.usage_usd > 0) ?? [],
);

async function open(participantId: number, name: string, accountId: number) {
  const sequence = ++requestSequence.value;
  participantName.value = name;
  data.value = null;
  message.value = "";
  loading.value = true;
  dialog.value?.showModal();
  try {
    const result = await api<APIUsageBreakdown>(
      `statistics/participants/${participantId}/api-usage?account_id=${accountId}`,
    );
    if (sequence === requestSequence.value) data.value = result;
  } catch (error) {
    if (sequence === requestSequence.value) {
      message.value =
        error instanceof ApiError ? error.message : "加载 API 用量失败";
    }
  } finally {
    if (sequence === requestSequence.value) loading.value = false;
  }
}

function close() {
  requestSequence.value += 1;
  dialog.value?.close();
}

defineExpose({ open, close });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box max-w-4xl">
      <form method="dialog">
        <button
          class="btn absolute top-3 right-3 btn-circle btn-ghost btn-sm"
          aria-label="关闭"
        >
          ✕
        </button>
      </form>

      <div class="flex items-center gap-2">
        <AppIcon name="chart-pie" class="size-5" />
        <h2 class="text-lg font-semibold">
          {{ participantName }}的 API 用量构成
        </h2>
        <span
          class="responsive-help-tooltip tooltip tooltip-bottom"
          data-tip="只统计当前上游周期。各 API 金额由本地原始请求事实按当前 FAST、长上下文与模型倍率规则汇总；修改规则可直接重算。该用量面板不参与粒子滤波，上游事实最多每小时刷新一次。"
        >
          <button
            type="button"
            class="btn btn-circle cursor-help btn-ghost btn-xs"
            aria-label="查看计算说明"
          >
            ?
          </button>
        </span>
      </div>

      <div v-if="loading" class="flex justify-center py-20">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div v-else-if="message" class="mt-5 alert alert-error">
        <AppIcon name="exclamation-triangle" class="size-5" />
        <span>{{ message }}</span>
      </div>
      <template v-else-if="data">
        <div class="mt-5 grid gap-3 sm:grid-cols-3">
          <div class="stat rounded-box bg-base-200 p-4">
            <div class="stat-title">参与者本周期用量</div>
            <div class="stat-value text-xl">
              {{ formatCurrency(data.participant_total_usd) }}
            </div>
          </div>
          <div class="stat rounded-box bg-base-200 p-4">
            <div class="stat-title">本周期周限折算</div>
            <div class="stat-value text-xl">
              {{ formatCurrency(data.weekly_total_estimate_usd) }}
            </div>
          </div>
          <div class="stat rounded-box bg-base-200 p-4">
            <div class="stat-title">参与者占总周限</div>
            <div class="stat-value text-xl">
              {{ formatPercent(data.participant_weekly_percent) }}
            </div>
          </div>
        </div>

        <div
          class="mt-5 grid items-start gap-5 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]"
        >
          <div class="rounded-box border border-base-300 bg-base-200 p-4">
            <div v-if="usedKeys.length" class="h-72">
              <APIUsageDonut :items="usedKeys" />
            </div>
            <div
              v-else
              class="flex h-72 items-center justify-center opacity-60"
            >
              当前周期尚无 API 用量
            </div>
          </div>

          <div class="overflow-x-auto rounded-box border border-base-300">
            <table class="table">
              <thead>
                <tr>
                  <th>API 密钥</th>
                  <th class="text-right">用量</th>
                  <th class="text-right">占参与者</th>
                  <th class="text-right">占总周限</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in data.api_keys"
                  :key="item.api_key_id ?? item.name"
                >
                  <td>
                    <div class="max-w-56 font-medium break-words">
                      {{ item.name }}
                    </div>
                    <div v-if="item.status" class="mt-1 text-xs opacity-50">
                      {{ item.status }}
                    </div>
                  </td>
                  <td class="text-right tabular-nums">
                    {{ formatCurrency(item.usage_usd) }}
                  </td>
                  <td class="text-right tabular-nums">
                    {{ formatPercent(item.participant_usage_percent) }}
                  </td>
                  <td class="text-right tabular-nums">
                    {{ formatPercent(item.weekly_quota_percent) }}
                  </td>
                </tr>
                <tr v-if="!data.api_keys.length">
                  <td colspan="4" class="py-10 text-center opacity-60">
                    该参与者尚无 API 密钥
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <p class="mt-4 text-xs opacity-60">
          统计区间：{{ dateTime(data.starts_at) }} 至
          {{ dateTime(data.observed_to) }}；成本口径：{{
            data.cost_basis === "actual" ? "实际扣费" : "标准扣费"
          }};<CorrectionAmount
            :breakdown="data"
            label="修正合计 "
          />。结论生成时间：{{ dateTime(data.observed_to) }}。
        </p>
      </template>

      <div class="modal-action">
        <button class="btn" @click="close">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
