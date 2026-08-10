<script setup lang="ts">
import { ref } from "vue";

import type { Participant } from "@/types";
import { formatCurrency, formatCurrencyRange } from "@/utils/formatters";

const props = defineProps<{
  adminUrl: string;
  applyingParticipantId: number | null;
}>();

const emit = defineEmits<{
  openAdmin: [];
  apply: [participant: Participant];
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const participant = ref<Participant | null>(null);

function canApplyRecommendation() {
  return (
    participant.value?.snapshot?.recommended_balance_usd != null &&
    participant.value.snapshot.recommended_balance_usd > 0
  );
}

function open(selectedParticipant: Participant) {
  participant.value = selectedParticipant;
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
  participant.value = null;
}

function openAdmin() {
  close();
  emit("openAdmin");
}

function apply() {
  if (participant.value && canApplyRecommendation()) {
    emit("apply", participant.value);
  }
}

defineExpose({ open, close });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box max-w-xl">
      <form method="dialog">
        <button
          class="btn absolute top-3 right-3 btn-circle btn-ghost btn-sm"
          aria-label="关闭"
        >
          ✕
        </button>
      </form>
      <h2 class="mb-4 card-title text-xl">处理额度建议</h2>
      <div class="grid min-h-80 grid-rows-2 gap-3">
        <button
          type="button"
          class="card h-full w-full border border-base-300 bg-base-200 text-left shadow-xs"
          @click="openAdmin"
        >
          <span class="card-body flex-row items-center gap-4">
            <AppIcon name="link" class="size-8 shrink-0 text-primary" />
            <span>
              <span class="card-title">跳转至 Admin API</span>
              <span class="mt-1 block text-sm opacity-60">{{ adminUrl }}</span>
            </span>
          </span>
        </button>
        <button
          type="button"
          class="card h-full w-full border border-base-300 bg-base-200 text-left shadow-xs disabled:opacity-50"
          :disabled="applyingParticipantId != null || !canApplyRecommendation()"
          @click="apply"
        >
          <span class="card-body flex-row items-center gap-4">
            <span
              v-if="applyingParticipantId != null"
              class="loading loading-lg loading-spinner"
            ></span>
            <AppIcon v-else name="bolt" class="size-8 shrink-0 text-primary" />
            <span>
              <span class="card-title">
                {{
                  applyingParticipantId != null ? "正在设置余额" : "一键设置"
                }}
              </span>
              <span class="mt-1 block text-sm opacity-60">
                <template
                  v-if="participant?.snapshot?.recommended_balance_usd != null"
                >
                  将 Sub2API 用户余额设置为建议值
                  {{
                    formatCurrency(participant.snapshot.recommended_balance_usd)
                  }}
                  <template
                    v-if="
                      participant.snapshot.recommended_balance_min_usd !=
                        null &&
                      participant.snapshot.recommended_balance_max_usd != null
                    "
                  >
                    （{{
                      participant.snapshot.allocation_model === "time_varying"
                        ? "90% 参考范围"
                        : "整数百分比参考范围"
                    }}
                    {{
                      formatCurrencyRange(
                        participant.snapshot.recommended_balance_min_usd,
                        participant.snapshot.recommended_balance_max_usd,
                        participant.snapshot.recommended_balance_usd,
                      )
                    }}）
                  </template>
                </template>
                <template v-else>当前没有可应用的额度建议</template>
              </span>
            </span>
          </span>
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
