<script setup lang="ts">
import { ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import { ApiError, api } from "@/services/api";
import type { FastCorrectionDetail, Observation } from "@/types";
import { formatCurrency } from "@/utils/formatters";

import type { DialogController } from "../types";

const dialog = ref<HTMLDialogElement | null>(null);
const data = ref<FastCorrectionDetail | null>(null);
const loading = ref(false);
const message = ref("");
const dateTime = useDateTime();

function requestCount(value: number | null) {
  return value === null ? "待重建" : String(value);
}

async function open(observation: Observation) {
  data.value = null;
  message.value = "";
  loading.value = true;
  dialog.value?.showModal();
  try {
    data.value = await api<FastCorrectionDetail>(
      `observations/${observation.id}/fast-correction`,
    );
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "加载 FAST 修正明细失败";
  } finally {
    loading.value = false;
  }
}

function close() {
  dialog.value?.close();
}

defineExpose<DialogController<[Observation]>>({ open, close });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box max-w-5xl">
      <form method="dialog">
        <button
          class="btn absolute top-3 right-3 btn-circle btn-ghost btn-sm"
          aria-label="关闭"
        >
          ✕
        </button>
      </form>
      <h2 class="pr-10 text-lg font-bold">FAST 修正明细</h2>

      <div v-if="loading" class="flex justify-center py-12">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div v-else-if="message" class="mt-4 alert alert-error">
        <AppIcon name="exclamation-triangle" class="size-5" />
        <span>{{ message }}</span>
      </div>
      <template v-else-if="data">
        <p class="mt-1 text-sm opacity-60">
          {{ dateTime(data.started_at) }} 至 {{ dateTime(data.ended_at) }} ·
          {{ data.cost_basis_label }}口径
        </p>

        <div
          class="stats mt-4 w-full stats-vertical bg-base-200 lg:stats-horizontal"
        >
          <div class="stat">
            <div class="stat-title">区间全部请求</div>
            <div class="stat-value text-2xl">
              {{ requestCount(data.request_count) }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-title">FAST 请求</div>
            <div class="stat-value text-2xl text-primary">
              {{ data.fast_request_count }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-title">非 FAST 请求</div>
            <div class="stat-value text-2xl">
              {{ requestCount(data.non_fast_request_count) }}
            </div>
          </div>
        </div>

        <div
          v-if="data.request_count === null"
          class="mt-4 alert text-sm alert-warning"
        >
          <AppIcon name="exclamation-triangle" class="size-5 shrink-0" />
          <span>
            这条历史记录由旧版本生成，尚未保存全部请求数；执行一次 FAST
            修正重建后即可补齐非 FAST 请求统计。
          </span>
        </div>
        <div
          v-if="data.collection_error"
          class="mt-4 alert text-sm alert-error"
        >
          <AppIcon name="exclamation-triangle" class="size-5 shrink-0" />
          <span>{{ data.collection_error }}</span>
        </div>

        <div
          class="mt-4 rounded-box border border-base-300 bg-base-200 p-4 text-center"
        >
          <div class="text-sm opacity-65">修正公式</div>
          <div class="mt-2 text-lg font-bold tabular-nums">
            {{ formatCurrency(data.fast_billed_cost_usd) }} × ({{
              data.upstream_fast_multiplier
            }}
            ÷ {{ data.sub2api_fast_multiplier }} − 1) =
            {{ formatCurrency(data.correction_usd) }}
          </div>
          <div class="mt-2 text-sm opacity-70">
            Sub2API FAST 成本 {{ formatCurrency(data.fast_billed_cost_usd) }} +
            修正 {{ formatCurrency(data.correction_usd) }} = 上游等效 FAST 成本
            {{ formatCurrency(data.corrected_fast_cost_usd) }}
          </div>
        </div>

        <div class="mt-4 overflow-x-auto">
          <table class="table min-w-[50rem] table-sm">
            <thead>
              <tr>
                <th>Sub2API 用户</th>
                <th>全部请求</th>
                <th>FAST</th>
                <th>非 FAST</th>
                <th>FAST 原成本</th>
                <th>修正</th>
                <th>修正后 FAST 成本</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in data.users" :key="item.sub2api_user_id">
                <td>
                  <div class="font-medium">{{ item.display_name }}</div>
                  <div class="text-xs opacity-60">
                    ID {{ item.sub2api_user_id }}
                  </div>
                </td>
                <td>{{ requestCount(item.request_count) }}</td>
                <td>{{ item.fast_request_count }}</td>
                <td>{{ requestCount(item.non_fast_request_count) }}</td>
                <td class="tabular-nums">
                  {{ formatCurrency(item.fast_billed_cost_usd) }}
                </td>
                <td class="font-medium tabular-nums">
                  {{ formatCurrency(item.correction_usd) }}
                </td>
                <td class="tabular-nums">
                  {{ formatCurrency(item.corrected_fast_cost_usd) }}
                </td>
              </tr>
              <tr v-if="data.users.length === 0">
                <td colspan="7" class="py-6 text-center opacity-60">
                  该区间没有请求日志
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <div class="modal-action">
        <button class="btn" @click="close">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
