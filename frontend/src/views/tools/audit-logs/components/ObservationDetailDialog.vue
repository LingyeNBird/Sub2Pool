<script setup lang="ts">
import { ref } from "vue";

import type { Observation } from "@/types";
import {
  formatCurrency,
  formatCurrencyRange,
  formatPercent,
} from "@/utils/formatters";

import type { DialogController } from "../types";

const dialog = ref<HTMLDialogElement | null>(null);
const observation = ref<Observation | null>(null);

function open(value: Observation) {
  observation.value = value;
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
}

defineExpose<DialogController<[Observation]>>({ open, close });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box max-w-4xl">
      <h2 class="text-lg font-bold">观测详情</h2>
      <p class="mt-1 text-sm opacity-60">{{ observation?.sample_note }}</p>
      <div v-if="observation?.excluded" class="mt-4 alert alert-warning">
        <AppIcon name="exclamation-triangle" class="size-5" />
        <span>
          此记录已排除，不参与计算。{{ observation.exclusion_reason }}
        </span>
      </div>
      <div v-if="observation?.is_manual_start" class="mt-4 alert alert-info">
        <AppIcon name="flag" class="size-5" />
        <span>
          此记录是管理员指定的区间起点。{{
            observation.manual_start_reason || "未填写起点说明"
          }}
        </span>
      </div>
      <div class="mt-4 overflow-x-auto">
        <table class="table table-sm">
          <thead>
            <tr>
              <th>参与者</th>
              <th>成本增量</th>
              <th>本次归属</th>
              <th>累计归属</th>
              <th>剩余权益</th>
              <th>建议用户余额</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in observation?.participants"
              :key="item.participant_id"
            >
              <td>{{ item.participant_name }}</td>
              <td>{{ formatCurrency(item.delta_cost) }}</td>
              <td>{{ formatPercent(item.charged_delta_percent) }}</td>
              <td>
                <div>{{ formatPercent(item.charged_cycle_percent) }}</div>
                <div
                  v-if="item.charged_percent_lower !== null"
                  class="text-xs opacity-50"
                >
                  90%：
                  {{ formatPercent(item.charged_percent_lower) }} –
                  {{ formatPercent(item.charged_percent_upper) }}
                </div>
              </td>
              <td>{{ formatPercent(item.remaining_share_percent) }}</td>
              <td>
                {{
                  formatCurrencyRange(
                    item.recommended_balance_min_usd,
                    item.recommended_balance_max_usd,
                    item.recommended_balance_usd,
                  )
                }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="modal-action">
        <button class="btn" @click="close">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
