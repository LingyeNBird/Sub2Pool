<script setup lang="ts">
import { ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import type { Observation } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

import type { DialogController } from "../types";

defineProps<{ submitting: boolean }>();
const emit = defineEmits<{
  confirm: [start: Observation, end: Observation, reason: string];
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const startObservation = ref<Observation | null>(null);
const endObservation = ref<Observation | null>(null);
const reason = ref("");
const dateTime = useDateTime();

function open(start: Observation, end: Observation) {
  startObservation.value = start;
  endObservation.value = end;
  reason.value = start.manual_start_reason || "";
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
}

function confirm() {
  if (startObservation.value && endObservation.value)
    emit("confirm", startObservation.value, endObservation.value, reason.value);
}

defineExpose<DialogController<[Observation, Observation]>>({ open, close });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">设置管理员起点区间</h2>
      <p class="mt-3 text-sm leading-6 opacity-70">
        开始记录提供周期的累计成本和上游百分比零基线。开始至结束记录（均包含）强制属于同一周期，区间内的
        0%、重置时间变化和其他起点不会再次切分周期。开始与结束可以选择同一条记录。
      </p>
      <div
        v-if="startObservation && endObservation"
        class="mt-4 grid gap-3 sm:grid-cols-2"
      >
        <div class="rounded-box bg-base-300 p-4">
          <div class="text-xs font-medium tracking-wide opacity-60">
            开始记录
          </div>
          <div class="mt-1 font-medium">
            {{ dateTime(startObservation.observed_at) }}
          </div>
          <div class="mt-1 text-sm opacity-70">
            上游已用
            {{ formatPercent(startObservation.upstream_used_percent) }} ·
            原始累计成本
            {{ formatCurrency(startObservation.raw_selected_total_cost) }}
          </div>
        </div>
        <div class="rounded-box bg-base-300 p-4">
          <div class="text-xs font-medium tracking-wide opacity-60">
            结束记录
          </div>
          <div class="mt-1 font-medium">
            {{ dateTime(endObservation.observed_at) }}
          </div>
          <div class="mt-1 text-sm opacity-70">
            上游已用
            {{ formatPercent(endObservation.upstream_used_percent) }} ·
            原始累计成本
            {{ formatCurrency(endObservation.raw_selected_total_cost) }}
          </div>
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
          确认起点区间
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
