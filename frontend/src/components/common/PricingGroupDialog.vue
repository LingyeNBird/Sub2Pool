<script setup lang="ts">
import { ref } from "vue";
import { api } from "@/services/api";

withDefaults(defineProps<{ description?: string }>(), {
  description:
    "只修改勾选分组，影响组内所有用户。取消勾选不会撤回旧配置；历史操作记录和撤回能力保留。",
});

const dialog = ref<HTMLDialogElement | null>(null);
const selectedGroups = ref<number[]>([]);
const availableGroups = ref<Array<{ id: number; name: string }>>([]);
const loading = ref(false);
const loaded = ref(false);
const saving = ref(false);
const groupsError = ref("");
const applyError = ref("");
let generation = 0;
let submit: ((groupIds: number[]) => Promise<string | null>) | null = null;

async function loadGroups() {
  const request = ++generation;
  loading.value = true;
  groupsError.value = "";
  try {
    const groups = await api<Array<{ id: number; name: string }>>(
      "settings/upstream-pricing?groups=1",
    );
    if (request !== generation) return;
    availableGroups.value = groups;
    const ids = new Set(groups.map((group) => group.id));
    selectedGroups.value = selectedGroups.value.filter((id) => ids.has(id));
    loaded.value = true;
  } catch (cause) {
    if (request === generation)
      groupsError.value =
        cause instanceof Error ? cause.message : "读取分组失败";
  } finally {
    if (request === generation) loading.value = false;
  }
}

function open(
  groupIds: number[],
  apply: (groupIds: number[]) => Promise<string | null>,
) {
  if (saving.value) return;
  submit = apply;
  selectedGroups.value = [...groupIds];
  availableGroups.value = [];
  loaded.value = false;
  applyError.value = "";
  dialog.value?.showModal();
  void loadGroups();
}

function closeSelection() {
  generation += 1;
  loading.value = false;
  submit = null;
}

function preventClose(event: Event) {
  if (saving.value) event.preventDefault();
}

async function confirmApply() {
  if (
    !submit ||
    saving.value ||
    loading.value ||
    groupsError.value ||
    !selectedGroups.value.length
  )
    return;
  saving.value = true;
  applyError.value = "";
  try {
    const failure = await submit([...selectedGroups.value]);
    if (failure) applyError.value = failure;
    else dialog.value?.close();
  } catch (cause) {
    applyError.value =
      cause instanceof Error ? cause.message : "上游计费应用失败";
  } finally {
    saving.value = false;
  }
}

defineExpose({ open });
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="modal"
      aria-label="选择目标分组并写入"
      @cancel="preventClose"
      @close="closeSelection"
    >
      <div class="modal-box max-w-xl">
        <h3 class="text-lg font-semibold">选择目标分组并写入</h3>
        <p class="mt-2 text-sm opacity-65">{{ description }}</p>
        <div class="mt-4 flex items-center justify-between gap-2">
          <span class="text-sm">已选择 {{ selectedGroups.length }} 个分组</span>
          <button
            type="button"
            class="btn btn-outline btn-xs"
            :disabled="saving || loading"
            @click="loadGroups"
          >
            重新读取
          </button>
        </div>
        <p v-if="loading" role="status" class="py-4 text-sm opacity-65">
          正在读取分组…
        </p>
        <p v-if="groupsError" role="alert" class="mt-3 text-sm text-error">
          {{ groupsError }}
        </p>
        <fieldset
          :disabled="saving || loading"
          class="mt-3 max-h-72 space-y-3 overflow-y-auto rounded-box border border-base-300 p-4"
        >
          <label
            v-for="group in availableGroups"
            :key="group.id"
            class="flex cursor-pointer items-center gap-3 text-sm"
          >
            <input
              v-model="selectedGroups"
              type="checkbox"
              class="checkbox checkbox-sm"
              :value="group.id"
            />
            <span class="min-w-0 break-words"
              >{{ group.name }}
              <span class="opacity-50">ID {{ group.id }}</span></span
            >
          </label>
          <p
            v-if="loaded && !availableGroups.length"
            class="text-sm opacity-60"
          >
            没有可选的 OpenAI 分组。
          </p>
        </fieldset>
        <p v-if="applyError" role="alert" class="mt-3 text-sm text-error">
          {{ applyError }}
        </p>
        <div class="modal-action">
          <button
            type="button"
            class="btn btn-ghost"
            :disabled="saving"
            @click="dialog?.close()"
          >
            取消
          </button>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="
              saving || loading || !!groupsError || !selectedGroups.length
            "
            @click="confirmApply"
          >
            <span
              v-if="saving"
              class="loading loading-xs loading-spinner"
            ></span
            >确认写入
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button :disabled="saving">取消</button>
      </form>
    </dialog>
  </Teleport>
</template>
