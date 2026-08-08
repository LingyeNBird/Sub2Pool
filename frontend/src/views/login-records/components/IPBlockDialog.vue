<script setup lang="ts">
import { ref } from "vue";

import type { BlockedIPAddress, BlockedIPSource } from "@/types";

import type { PendingBlockAction } from "../types";

defineProps<{
  saving: boolean;
}>();

const emit = defineEmits<{
  confirm: [action: PendingBlockAction, notes: string];
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const notes = ref("");
const pendingAction = ref<PendingBlockAction | null>(null);

function openBlock(
  address: string,
  sourceType: BlockedIPSource,
  sourceLabel: string,
  eventId: number,
) {
  notes.value = "";
  pendingAction.value = {
    mode: "block",
    address,
    sourceType,
    sourceLabel,
    eventId,
    blockId: null,
  };
  dialog.value?.showModal();
}

function openUnblock(item: BlockedIPAddress) {
  pendingAction.value = {
    mode: "unblock",
    address: item.address,
    sourceType: item.source_type,
    sourceLabel: item.source_label,
    eventId: item.login_event_id,
    blockId: item.id,
  };
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
  pendingAction.value = null;
}

function confirm() {
  if (pendingAction.value) {
    emit("confirm", pendingAction.value, notes.value);
  }
}

defineExpose({ openBlock, openUnblock, close });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box">
      <form method="dialog">
        <button
          class="btn absolute top-3 right-3 btn-circle btn-ghost btn-sm"
          aria-label="关闭"
        >
          ✕
        </button>
      </form>
      <template v-if="pendingAction">
        <h3 class="text-lg font-bold">
          {{ pendingAction.mode === "block" ? "确认封禁地址" : "确认解除封禁" }}
        </h3>
        <div class="mt-4 rounded-box bg-base-200 p-4">
          <div class="text-sm opacity-60">{{ pendingAction.sourceLabel }}</div>
          <div class="mt-1 font-mono">{{ pendingAction.address }}</div>
        </div>
        <p class="mt-4 text-sm opacity-70">
          <template v-if="pendingAction.mode === 'block'">
            {{
              pendingAction.sourceType === "webrtc"
                ? "浏览器上报该地址后，页面将立即保持空白，后续登录请求不返回正文。首次页面请求发生在上报前，服务端无法提前识别这个地址。"
                : "命中该服务端可见地址时，所有路由都不会返回页面或响应正文。"
            }}
          </template>
          <template v-else>解除后，该地址可再次访问对应路径。</template>
        </p>
        <fieldset v-if="pendingAction.mode === 'block'" class="mt-3 fieldset">
          <label class="label" for="block-notes">备注</label>
          <input
            id="block-notes"
            v-model="notes"
            class="input w-full"
            maxlength="255"
            placeholder="例如：连续登录失败"
          />
        </fieldset>
      </template>
      <div class="modal-action">
        <button class="btn" :disabled="saving" @click="close">取消</button>
        <button
          class="btn"
          :class="pendingAction?.mode === 'block' ? 'btn-error' : 'btn-primary'"
          :disabled="saving"
          @click="confirm"
        >
          <span v-if="saving" class="loading loading-xs loading-spinner"></span>
          {{ pendingAction?.mode === "block" ? "确认封禁" : "确认解除" }}
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
