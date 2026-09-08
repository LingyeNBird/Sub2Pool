<script setup lang="ts">
import { ref } from "vue";
import { api } from "@/services/api";
import type { CPAClaim, CPAPoolSummary } from "@/types/cpa";
import { formatCurrency } from "@/utils/formatters";
import { useDateTime } from "@/composables/useDateTime";
const props = defineProps<{ account: CPAPoolSummary["accounts"][number] }>();
const emit = defineEmits<{ refresh: [] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const plan = ref<CPAClaim | null>(null);
const busy = ref(false);
const message = ref("");
const formatTime = useDateTime();
async function preview() {
  busy.value = true;
  message.value = "";
  plan.value = null;
  dialog.value?.showModal();
  try {
    plan.value = await api<CPAClaim>(
      `cpa/unassigned/preview?account_id=${props.account.account_id}`,
      { method: "POST" },
    );
  } catch (error) {
    message.value = error instanceof Error ? error.message : "预览失败";
  } finally {
    busy.value = false;
  }
}
async function apply() {
  if (!plan.value) return;
  busy.value = true;
  try {
    await api(`cpa/claims/${plan.value.id}/apply`, { method: "POST" });
    dialog.value?.close();
    message.value = "已计入车主，历史额度已重算。";
    plan.value = null;
    emit("refresh");
  } catch (error) {
    message.value = error instanceof Error ? error.message : "认领失败";
    plan.value = null;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <button
    v-if="account.owner.status === 'active'"
    class="btn mt-3 self-start btn-sm"
    :disabled="busy"
    @click="preview"
  >
    认领历史未归属请求
  </button>
  <dialog
    ref="dialog"
    class="modal"
    :aria-label="`${account.account_name}的车主历史认领`"
    @cancel="busy && $event.preventDefault()"
  >
    <div class="modal-box">
      <h3 class="text-lg font-semibold">
        历史未归属请求 → {{ account.owner.participant_name }}
      </h3>
      <p class="mt-2 text-sm text-base-content/60">
        仅处理 {{ account.account_name }} 已采集且没有归属的请求。成员 Key
        的已有归属保持不变。
      </p>
      <p v-if="busy" role="status" class="mt-4">正在处理…</p>
      <p v-if="message" role="alert" class="mt-4 alert">{{ message }}</p>
      <div v-if="plan" class="mt-4 space-y-3">
        <p class="text-xs text-base-content/60">
          {{ formatTime(plan.started_at) }} 至 {{ formatTime(plan.ended_at) }}
        </p>
        <div
          v-for="row in plan.accounts"
          :key="row.account_id"
          class="card bg-base-200"
        >
          <div class="card-body gap-2 p-4">
            <p class="font-medium">
              {{ row.request_count.toLocaleString() }} 次请求 ·
              {{ row.token_count.toLocaleString() }} Token
            </p>
            <p>估算费用 {{ formatCurrency(row.usage_usd) }}</p>
            <p v-if="row.unpriced_request_count" class="text-sm">
              {{ row.unpriced_request_count }} 次缺价，未计入费用
            </p>
            <p v-if="!row.coverage.complete" class="text-sm">
              采集不完整（{{ row.coverage.gaps.length }}
              段缺口），只认领已有请求。
            </p>
          </div>
        </div>
        <p class="text-sm">{{ plan.historical_contract_policy }}</p>
      </div>
      <div class="modal-action">
        <button class="btn" :disabled="busy" @click="dialog?.close()">
          关闭
        </button>
        <button v-if="!plan && !busy" class="btn" @click="preview">
          重新预览
        </button>
        <button
          v-if="plan"
          class="btn btn-primary"
          :disabled="busy"
          @click="apply"
        >
          确认计入 {{ account.owner.participant_name }}
        </button>
      </div>
    </div>
  </dialog>
</template>
