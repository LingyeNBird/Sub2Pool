<script setup lang="ts">
import { ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";

import type { Observation } from "@/types";
import {
  formatCurrency,
  formatCurrencyRange,
  formatPercent,
} from "@/utils/formatters";

import type { DialogController } from "../types";

const dialog = ref<HTMLDialogElement | null>(null);
const observation = ref<Observation | null>(null);
const dateTime = useDateTime();

function intervalSourceLabel(value: string) {
  return (
    {
      window_total: "查询窗口累计值",
      snapshot_delta: "同一查询窗口快照差",
      request_logs: "请求日志精确汇总",
      historical_anchor: "历史锚点",
      historical_logs: "历史请求日志",
      unresolved: "尚未解析",
    }[value] ??
    value ??
    "—"
  );
}

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
      <section v-if="observation" class="mt-5">
        <h3 class="font-semibold">成本事实</h3>
        <div class="mt-2 grid gap-3 sm:grid-cols-2">
          <div class="rounded-box bg-base-200 p-4">
            <div class="text-xs opacity-60">原始累计成本</div>
            <div class="mt-1 font-semibold tabular-nums">
              {{ formatCurrency(observation.raw_selected_total_cost) }}
            </div>
            <div class="mt-1 text-xs opacity-60">
              <template
                v-if="
                  observation.cost_window_started_at &&
                  observation.cost_window_ended_at
                "
              >
                {{ dateTime(observation.cost_window_started_at) }} 至
                {{ dateTime(observation.cost_window_ended_at) }}
              </template>
              <template v-else>旧记录未保存原始查询窗口</template>
            </div>
          </div>
          <div class="rounded-box bg-base-200 p-4">
            <div class="text-xs opacity-60">本次成本区间</div>
            <div class="mt-1 font-semibold tabular-nums">
              {{ formatCurrency(observation.interval_cost) }}
            </div>
            <div class="mt-1 text-xs opacity-60">
              <template v-if="observation.interval_cost_started_at">
                {{ dateTime(observation.interval_cost_started_at) }} 至
                {{ dateTime(observation.observed_at) }} ·
              </template>
              {{ intervalSourceLabel(observation.interval_cost_source) }}
            </div>
          </div>
          <div class="rounded-box bg-base-200 p-4 sm:col-span-2">
            <div class="text-xs opacity-60">归一化周期累计成本</div>
            <div class="mt-1 font-semibold tabular-nums">
              {{ formatCurrency(observation.normalized_total_cost) }}
            </div>
            <div class="mt-1 text-xs opacity-60">
              模型使用区间增量衔接后的同一成本坐标；原始累计快照不会被改写。
            </div>
          </div>
        </div>
      </section>
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
