<script setup lang="ts">
import { computed, ref } from "vue";

import type {
  ConfirmDialogHandle,
  ConfirmDialogOptions,
  ConfirmDialogTone,
} from "@/types/common";

const dialog = ref<HTMLDialogElement | null>(null);
const options = ref<Required<ConfirmDialogOptions>>({
  title: "请确认操作",
  message: "",
  confirmLabel: "确认",
  cancelLabel: "取消",
  tone: "primary",
});
let resolvePending: ((confirmed: boolean) => void) | null = null;

const confirmClass = computed(() => {
  const classes: Record<ConfirmDialogTone, string> = {
    primary: "btn-primary",
    warning: "btn-warning",
    error: "btn-error",
  };
  return classes[options.value.tone];
});

function settle(confirmed: boolean) {
  const resolve = resolvePending;
  resolvePending = null;
  if (dialog.value?.open) dialog.value.close();
  resolve?.(confirmed);
}

function open(value: ConfirmDialogOptions): Promise<boolean> {
  if (resolvePending) settle(false);
  options.value = {
    title: value.title,
    message: value.message,
    confirmLabel: value.confirmLabel ?? "确认",
    cancelLabel: value.cancelLabel ?? "取消",
    tone: value.tone ?? "primary",
  };
  dialog.value?.showModal();
  return new Promise((resolve) => {
    resolvePending = resolve;
  });
}

function close() {
  settle(false);
}

function handleNativeClose() {
  if (!resolvePending) return;
  const resolve = resolvePending;
  resolvePending = null;
  resolve(false);
}

defineExpose<ConfirmDialogHandle>({ open, close });
</script>

<template>
  <dialog
    ref="dialog"
    class="modal"
    @cancel.prevent="settle(false)"
    @close="handleNativeClose"
  >
    <div class="modal-box max-w-lg">
      <div class="flex items-start gap-3">
        <AppIcon
          :name="
            options.tone === 'primary'
              ? 'information-circle'
              : 'exclamation-triangle'
          "
          class="mt-0.5 size-6 shrink-0"
          :class="{
            'text-primary': options.tone === 'primary',
            'text-warning': options.tone === 'warning',
            'text-error': options.tone === 'error',
          }"
        />
        <div class="min-w-0">
          <h2 class="text-lg font-bold">{{ options.title }}</h2>
          <p class="mt-3 text-sm leading-6 whitespace-pre-line opacity-70">
            {{ options.message }}
          </p>
        </div>
      </div>
      <div class="modal-action">
        <button type="button" class="btn" @click="settle(false)">
          {{ options.cancelLabel }}
        </button>
        <button
          type="button"
          class="btn"
          :class="confirmClass"
          @click="settle(true)"
        >
          {{ options.confirmLabel }}
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop">
      <button type="button" @click="settle(false)">关闭</button>
    </form>
  </dialog>
</template>
