<script setup lang="ts">
import { computed, nextTick, ref, useId } from "vue";
import type { CorrectionBreakdown } from "@/types/common";
import { correctionTotal, formatCorrectionCurrency } from "@/utils/formatters";
import CorrectionDetails from "./CorrectionDetails.vue";

defineOptions({ inheritAttrs: false });
const props = withDefaults(
  defineProps<{ breakdown: CorrectionBreakdown; label?: string }>(),
  { label: "" },
);
const value = computed(() => correctionTotal(props.breakdown));
const dialog = ref<HTMLDialogElement | null>(null);
const mounted = ref(false);
const titleId = useId();
async function open() {
  mounted.value = true;
  await nextTick();
  if (!dialog.value?.open) dialog.value?.showModal();
}
</script>

<template>
  <button
    v-bind="$attrs"
    type="button"
    class="link cursor-pointer font-medium tabular-nums link-hover"
    aria-haspopup="dialog"
    aria-label="展开修正合计明细"
    @click.stop="open"
  >
    <slot>{{ label }}{{ formatCorrectionCurrency(value) }}</slot>
  </button>
  <Teleport to="body">
    <dialog
      v-if="mounted"
      ref="dialog"
      class="modal"
      :aria-labelledby="titleId"
      @close="mounted = false"
    >
      <div class="modal-box max-w-lg">
        <h2 :id="titleId" class="mb-4 text-lg font-bold">修正合计明细</h2>
        <CorrectionDetails :breakdown="breakdown" />
        <div class="modal-action">
          <form method="dialog"><button class="btn">关闭</button></form>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
    </dialog>
  </Teleport>
</template>
