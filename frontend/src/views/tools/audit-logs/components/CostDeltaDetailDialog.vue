<script setup lang="ts">
import { computed, ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import type { Observation } from "@/types/observations";
import { formatCurrency } from "@/utils/formatters";

import type { DialogController } from "../types";

const dialog = ref<HTMLDialogElement | null>(null);
const observation = ref<Observation | null>(null);
const dateTime = useDateTime();

const participantDeltaTotal = computed(() =>
  Number(
    (
      observation.value?.participants.reduce(
        (total, item) => total + (item.delta_cost ?? 0),
        0,
      ) ?? 0
    ).toFixed(6),
  ),
);
const unmatchedCostDelta = computed(() => {
  if (observation.value?.delta_cost == null) return null;
  const difference = observation.value.delta_cost - participantDeltaTotal.value;
  return Math.abs(difference) < 0.000001 ? 0 : Number(difference.toFixed(6));
});

function open(value: Observation) {
  if (value.delta_cost == null) return;
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
    <div class="modal-box max-w-3xl">
      <h2 class="text-lg font-bold">成本增量明细</h2>
      <p v-if="observation" class="mt-1 text-sm opacity-60">
        {{ dateTime(observation.observed_at) }} 相对上一条有效观测
      </p>
      <div
        v-if="observation?.provider === 'sub2api'"
        class="mt-4 overflow-x-auto"
      >
        <table class="table table-sm">
          <thead>
            <tr>
              <th>参与者</th>
              <th>上一点累计成本</th>
              <th>当前累计成本</th>
              <th>成本增量</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in observation?.participants"
              :key="item.participant_id"
            >
              <td>{{ item.participant_name }}</td>
              <td class="tabular-nums">
                {{
                  item.delta_cost === null
                    ? "无上一观测快照"
                    : formatCurrency(item.selected_cost - item.delta_cost)
                }}
              </td>
              <td class="tabular-nums">
                {{ formatCurrency(item.selected_cost) }}
              </td>
              <td class="font-medium tabular-nums">
                {{ formatCurrency(item.delta_cost) }}
              </td>
            </tr>
            <tr v-if="observation?.participants.length === 0">
              <td colspan="4" class="py-6 text-center opacity-60">
                此观测没有参与者快照
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <th colspan="3">已知参与者成本增量合计</th>
              <th class="tabular-nums">
                {{ formatCurrency(participantDeltaTotal) }}
              </th>
            </tr>
            <tr>
              <th colspan="3">账号总成本增量</th>
              <th class="tabular-nums">
                {{ formatCurrency(observation?.delta_cost) }}
              </th>
            </tr>
            <tr v-if="unmatchedCostDelta !== null && unmatchedCostDelta !== 0">
              <th colspan="3">未映射或无法逐用户还原</th>
              <th class="tabular-nums">
                {{ formatCurrency(unmatchedCostDelta) }}
              </th>
            </tr>
          </tfoot>
        </table>
      </div>
      <div
        v-if="observation?.provider === 'cpa'"
        class="stats mt-4 w-full bg-base-200"
      >
        <div class="stat">
          <div class="stat-title">账号总成本增量</div>
          <div class="stat-value text-2xl">
            {{ formatCurrency(observation.delta_cost) }}
          </div>
          <div class="stat-desc">来自连接后采集的 CPA usage 事件</div>
        </div>
      </div>
      <div
        v-if="
          observation?.participants.some((item) => item.delta_cost === null)
        "
        class="mt-4 alert text-sm alert-info"
      >
        <AppIcon name="information-circle" class="size-5" />
        <span>
          首次出现在观测中的参与者没有上一点快照；系统会保留其当前周期累计成本，但不会伪造该采样区间的个人增量。
        </span>
      </div>
      <div class="modal-action">
        <button class="btn" @click="close">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
