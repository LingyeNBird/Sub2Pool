<script setup lang="ts">
import { reactive, ref } from "vue";

import type {
  DialogController,
  ObservationFilterKind,
  ObservationFilters,
} from "../types";

const emit = defineEmits<{
  apply: [filters: ObservationFilters];
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const filterKind = ref<ObservationFilterKind>("time");
const draft = reactive<ObservationFilters>({
  from: "",
  to: "",
  source: "",
  query_mode: "",
});

function open(kind: ObservationFilterKind, filters: ObservationFilters) {
  filterKind.value = kind;
  Object.assign(draft, filters);
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
}

function apply() {
  emit("apply", { ...draft });
  close();
}

function clear() {
  if (filterKind.value === "time") {
    draft.from = "";
    draft.to = "";
  } else if (filterKind.value === "source") {
    draft.source = "";
  } else {
    draft.query_mode = "";
  }
  apply();
}

defineExpose<DialogController<[ObservationFilterKind, ObservationFilters]>>({
  open,
  close,
});
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">
        {{
          filterKind === "time"
            ? "筛选观测时间"
            : filterKind === "source"
              ? "筛选观测来源"
              : "筛选查询方式"
        }}
      </h2>
      <div v-if="filterKind === 'time'" class="mt-4 grid gap-3 sm:grid-cols-2">
        <fieldset class="fieldset">
          <label class="label">起始日期时间</label>
          <input
            v-model="draft.from"
            type="datetime-local"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label">终止日期时间</label>
          <input
            v-model="draft.to"
            type="datetime-local"
            class="input w-full"
          />
        </fieldset>
      </div>
      <fieldset v-else-if="filterKind === 'source'" class="mt-4 fieldset">
        <label class="label">来源</label>
        <select v-model="draft.source" class="select w-full">
          <option value="">全部来源</option>
          <option value="manual">手动</option>
          <option value="scheduled">定时</option>
          <option value="exhausted">额度耗尽</option>
          <option value="reset">临近重置</option>
        </select>
      </fieldset>
      <fieldset v-else class="mt-4 fieldset">
        <label class="label">查询方式</label>
        <select v-model="draft.query_mode" class="select w-full">
          <option value="">全部方式</option>
          <option value="passive">被动快照</option>
          <option value="direct">上游直查</option>
        </select>
      </fieldset>
      <div class="modal-action">
        <button type="button" class="btn btn-ghost" @click="clear">
          清除筛选
        </button>
        <button type="button" class="btn" @click="close">取消</button>
        <button type="button" class="btn btn-primary" @click="apply">
          应用
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
