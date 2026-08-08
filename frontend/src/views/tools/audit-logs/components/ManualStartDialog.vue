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
      <h2 class="text-lg font-bold">设置管理员区间起点</h2>
      <p class="mt-3 text-sm leading-6 opacity-70">
        管理员起点优先于上游重置时间。系统会把所选观测的累计成本和上游百分比作为零基线，只重算该点及其后续记录；请仅在确认这里发生了官方赠送刷新或其他真实边界时使用。
      </p>
      <div v-if="observation" class="mt-4 rounded-box bg-base-300 p-4">
        <div class="font-medium">
          {{ dateTime(observation.observed_at) }}
        </div>
        <div class="mt-1 text-sm opacity-70">
          上游已用 {{ formatPercent(observation.upstream_used_percent) }} ·
          原始累计成本
          {{ formatCurrency(observation.raw_selected_total_cost) }}
        </div>
      </div>
      <fieldset class="mt-4 fieldset">
        <label class="label">起点说明（可选）</label>
        <input
          v-model="reason"
          class="input w-full"
          maxlength="255"
          placeholder="例如：管理员确认此处为官方赠送刷新"
        />
      </fieldset>
      <div class="modal-action">
        <button type="button" class="btn" :disabled="submitting" @click="close">
          取消
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="submitting"
          @click="confirm"
        >
          <span
            v-if="submitting"
            class="loading loading-xs loading-spinner"
          ></span>
          确认设为起点
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
