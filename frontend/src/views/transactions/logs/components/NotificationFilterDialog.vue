<script setup lang="ts">
import { reactive, ref } from "vue";

import type {
  NotificationFilterKind,
  NotificationFilterOptions,
  NotificationFilters,
} from "../types";

const props = defineProps<{
  options: NotificationFilterOptions;
}>();

const emit = defineEmits<{
  apply: [filters: NotificationFilters];
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const filterKind = ref<NotificationFilterKind>("time");
const draft = reactive<NotificationFilters>({
  from: "",
  to: "",
  event_type: "",
  participant: "",
  subject: "",
  status: "",
});

function open(kind: NotificationFilterKind, filters: NotificationFilters) {
  filterKind.value = kind;
  Object.assign(draft, filters);
  dialog.value?.showModal();
}

function apply() {
  emit("apply", { ...draft });
  dialog.value?.close();
}

function clear() {
  if (filterKind.value === "time") {
    draft.from = "";
    draft.to = "";
  } else {
    const key = filterKind.value === "type" ? "event_type" : filterKind.value;
    draft[key] = "";
  }
  apply();
}

defineExpose({ open });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">
        {{
          filterKind === "time"
            ? "筛选通知时间"
            : filterKind === "type"
              ? "筛选通知类型"
              : filterKind === "participant"
                ? "筛选参与者"
                : filterKind === "subject"
                  ? "搜索主题"
                  : "筛选发送状态"
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
      <fieldset v-else-if="filterKind === 'type'" class="mt-4 fieldset">
        <label class="label">类型</label>
        <select v-model="draft.event_type" class="select w-full">
          <option value="">全部类型</option>
          <option
            v-for="option in props.options.types"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>
      </fieldset>
      <fieldset v-else-if="filterKind === 'participant'" class="mt-4 fieldset">
        <label class="label">参与者</label>
        <select v-model="draft.participant" class="select w-full">
          <option value="">全部参与者</option>
          <option value="system">系统</option>
          <option
            v-for="participant in props.options.participants"
            :key="participant.id"
            :value="String(participant.id)"
          >
            {{ participant.name }}
          </option>
        </select>
      </fieldset>
      <fieldset v-else-if="filterKind === 'subject'" class="mt-4 fieldset">
        <label class="label">主题关键词</label>
        <input
          v-model.trim="draft.subject"
          class="input w-full"
          placeholder="输入部分主题文字"
        />
      </fieldset>
      <fieldset v-else class="mt-4 fieldset">
        <label class="label">发送状态</label>
        <select v-model="draft.status" class="select w-full">
          <option value="">全部状态</option>
          <option
            v-for="option in props.options.statuses"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>
      </fieldset>
      <div class="modal-action">
        <button type="button" class="btn btn-ghost" @click="clear">
          清除筛选
        </button>
        <button type="button" class="btn" @click="dialog?.close()">取消</button>
        <button type="button" class="btn btn-primary" @click="apply">
          应用
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
