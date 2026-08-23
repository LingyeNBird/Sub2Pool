<script setup lang="ts">
import { ref } from "vue";

import type { NotificationRecord } from "@/types/security";

const dialog = ref<HTMLDialogElement | null>(null);
const selected = ref<NotificationRecord | null>(null);

function open(record: NotificationRecord) {
  selected.value = record;
  dialog.value?.showModal();
}

defineExpose({ open });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">{{ selected?.subject }}</h2>
      <div class="mt-4 text-sm leading-6 whitespace-pre-wrap">
        {{ selected?.body }}
      </div>
      <div
        v-if="selected?.error"
        class="mt-4 alert alert-soft text-sm alert-error"
      >
        {{ selected.error }}
      </div>
      <div class="modal-action">
        <button class="btn" @click="dialog?.close()">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
