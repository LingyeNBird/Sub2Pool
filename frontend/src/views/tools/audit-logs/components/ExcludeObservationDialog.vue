<script setup lang="ts">
import { ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import type { Observation } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

import type { DialogController } from "../types";

defineProps<{ submitting: boolean }>();
const emit = defineEmits<{
  confirm: [observation: Observation, reason: string];
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const observation = ref<Observation | null>(null);
const reason = ref("");
const dateTime = useDateTime();

function open(value: Observation) {
  observation.value = value;
  reason.value = "";
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
}

function confirm() {
  if (observation.value) emit("confirm", observation.value, reason.value);
}

defineExpose<DialogController<[Observation]>>({ open, close });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">排除校准记录</h2>
      <p class="mt-3 text-sm opacity-70">
        原始记录会永久保留作审计。系统将忽略该点，并只从它所在的最早受影响区间起点向后重算；更早的稳定区间不会重复重放。
      </p>
      <div v-if="observation" class="mt-4 rounded-box bg-base-300 p-4">
        <div class="font-medium">
          {{ dateTime(observation.observed_at) }}
        </div>
        <div class="mt-1 text-sm opacity-70">
          上游已用 {{ formatPercent(observation.upstream_used_percent) }} ·
          累计成本 {{ formatCurrency(observation.selected_total_cost) }}
        </div>
      </div>
      <fieldset class="mt-4 fieldset">
        <label class="label">排除原因（可选）</label>
        <input
          v-model="reason"
          class="input w-full"
          maxlength="255"
          placeholder="例如：上游返回了一次异常百分比"
        />
      </fieldset>
      <div class="modal-action">
        <button type="button" class="btn" :disabled="submitting" @click="close">
          取消
        </button>
        <button
          type="button"
          class="btn btn-warning"
          :disabled="submitting"
          @click="confirm"
        >
          <span
            v-if="submitting"
            class="loading loading-xs loading-spinner"
          ></span>
          确认排除
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
