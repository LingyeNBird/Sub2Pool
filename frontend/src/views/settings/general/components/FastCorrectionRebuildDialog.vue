<script setup lang="ts">
import { ref } from "vue";

export type FastCorrectionRebuildScope = "cycle" | "all";

defineProps<{ rebuilding: boolean }>();
const emit = defineEmits<{
  confirm: [scope: FastCorrectionRebuildScope];
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const recommended = ref(false);

function open(isRecommended = false) {
  recommended.value = isRecommended;
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
}

defineExpose({ open, close });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box max-w-xl">
      <form method="dialog">
        <button
          class="btn absolute top-3 right-3 btn-circle btn-ghost btn-sm"
          :disabled="rebuilding"
          aria-label="关闭"
        >
          ✕
        </button>
      </form>
      <h3 class="pr-10 text-lg font-bold">选择 FAST 修正重建范围</h3>
      <div v-if="recommended" class="mt-4 alert text-sm alert-warning">
        <AppIcon name="exclamation-triangle" class="size-5 shrink-0" />
        <span>检测到当前周期存在未计算区间，建议从当前周期开始重建。</span>
      </div>

      <div class="mt-5 grid gap-3">
        <button
          type="button"
          class="card min-h-32 w-full cursor-pointer border border-base-300 bg-base-200 text-left transition-colors hover:border-primary"
          :disabled="rebuilding"
          @click="emit('confirm', 'cycle')"
        >
          <span class="card-body justify-center">
            <span class="card-title">从当前周期开始</span>
            <span class="text-sm leading-6 opacity-70">
              读取当前归属区间起点至最新采样点的请求日志，适合补齐刚开启 FAST
              修正后的当前周期。
            </span>
            <span
              v-if="rebuilding"
              class="loading loading-sm loading-spinner"
            ></span>
          </span>
        </button>
        <button
          type="button"
          class="card min-h-32 w-full cursor-pointer border border-base-300 bg-base-200 text-left transition-colors hover:border-primary"
          :disabled="rebuilding"
          @click="emit('confirm', 'all')"
        >
          <span class="card-body justify-center">
            <span class="card-title">从全部记录开始</span>
            <span class="text-sm leading-6 opacity-70">
              从 Sub2API 可读取的最早请求日志开始重建，耗时取决于历史请求数量。
            </span>
            <span
              v-if="rebuilding"
              class="loading loading-sm loading-spinner"
            ></span>
          </span>
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop">
      <button :disabled="rebuilding">关闭</button>
    </form>
  </dialog>
</template>
