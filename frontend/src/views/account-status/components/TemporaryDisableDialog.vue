<script setup lang="ts">
import { computed, ref, watch } from "vue";

import AppIcon from "@/components/common/AppIcon.vue";
import { api, jsonBody } from "@/services/api";
import type { AccountStatusAccount, TemporaryDisable } from "@/types/accounts";

import {
  DEFAULT_DISABLE_MINUTES,
  MAX_DISABLE_MINUTES,
  MAX_REMAINING_TEXT,
  disableMinutes,
  formatRemaining,
  minutesToDuration,
  remainingSeconds,
  type DurationUnit,
} from "../temporaryDisable";

const props = defineProps<{ now: number }>();
const emit = defineEmits<{ changed: [] }>();

const scopeDialog = ref<HTMLDialogElement | null>(null);
const durationDialog = ref<HTMLDialogElement | null>(null);
const account = ref<AccountStatusAccount | null>(null);
const editing = ref<TemporaryDisable | null>(null);
const scope = ref<"account" | "model">("account");
const model = ref("");
const models = ref<string[]>([]);
const loadingModels = ref(false);
const amount = ref(DEFAULT_DISABLE_MINUTES / 60);
const unit = ref<DurationUnit>("hours");
const saving = ref(false);
const message = ref("");

const minutes = computed(() => disableMinutes(amount.value, unit.value));
const canConfirmDuration = computed(
  () => minutes.value != null && !saving.value,
);
const remainingLabel = computed(() => {
  const current = editing.value;
  if (!current) return "";
  const text = formatRemaining(remainingSeconds(current.restore_at, props.now));
  return text && text !== MAX_REMAINING_TEXT ? `剩余 ${text}` : "已到恢复时刻";
});

// 只有禁用模型才需要上游模型列表；按账号缓存，避免反复读取上游。
watch(scope, (value) => {
  if (value === "model") void loadModels();
});

async function loadModels() {
  const current = account.value;
  if (!current || loadingModels.value) return;
  loadingModels.value = true;
  message.value = "";
  try {
    const data = await api<{ models: string[] }>(
      `accounts/${current.id}/models`,
    );
    models.value = data.models;
    if (!models.value.includes(model.value)) model.value = "";
    if (!models.value.length) message.value = "该账号当前没有可禁用的模型";
  } catch (error) {
    message.value =
      error instanceof Error ? error.message : "读取账号模型列表失败";
  } finally {
    loadingModels.value = false;
  }
}

function openCreate(target: AccountStatusAccount) {
  account.value = target;
  editing.value = null;
  scope.value = "account";
  model.value = "";
  models.value = [];
  amount.value = DEFAULT_DISABLE_MINUTES / 60;
  unit.value = "hours";
  message.value = "";
  scopeDialog.value?.showModal();
}

function openEdit(target: AccountStatusAccount, disable: TemporaryDisable) {
  account.value = target;
  editing.value = disable;
  const seconds = remainingSeconds(disable.restore_at, props.now) ?? 0;
  const preset = minutesToDuration(
    Math.min(MAX_DISABLE_MINUTES, Math.max(1, Math.ceil(seconds / 60))),
  );
  amount.value = preset.amount;
  unit.value = preset.unit;
  message.value = "";
  durationDialog.value?.showModal();
}

function confirmScope() {
  if (scope.value === "model" && !model.value) {
    message.value = "请选择要禁用的模型";
    return;
  }
  message.value = "";
  const preset = minutesToDuration(DEFAULT_DISABLE_MINUTES);
  amount.value = preset.amount;
  unit.value = preset.unit;
  durationDialog.value?.showModal();
}

function closeScope() {
  message.value = "";
  scopeDialog.value?.close();
}

function closeDuration() {
  message.value = "";
  durationDialog.value?.close();
}

async function confirmDuration() {
  const current = account.value;
  const target = minutes.value;
  if (!current || target == null || saving.value) return;
  saving.value = true;
  message.value = "";
  try {
    if (editing.value) {
      await api(`accounts/temporary-disables/${editing.value.id}`, {
        method: "PATCH",
        body: jsonBody({ minutes: target }),
      });
    } else {
      await api(`accounts/${current.id}/temporary-disables`, {
        method: "POST",
        body: jsonBody({
          scope: scope.value,
          model: scope.value === "model" ? model.value : "",
          minutes: target,
        }),
      });
    }
    durationDialog.value?.close();
    scopeDialog.value?.close();
    emit("changed");
  } catch (error) {
    message.value = error instanceof Error ? error.message : "临时禁用操作失败";
  } finally {
    saving.value = false;
  }
}

async function restoreNow() {
  const current = editing.value;
  if (!current || saving.value) return;
  saving.value = true;
  message.value = "";
  try {
    await api(`accounts/temporary-disables/${current.id}`, {
      method: "DELETE",
    });
    durationDialog.value?.close();
    emit("changed");
  } catch (error) {
    message.value = error instanceof Error ? error.message : "提前恢复失败";
  } finally {
    saving.value = false;
  }
}

defineExpose({ openCreate, openEdit });
</script>

<template>
  <dialog ref="scopeDialog" class="modal" @cancel.prevent="closeScope">
    <div class="modal-box w-[calc(100vw-2rem)] max-w-lg">
      <h2 class="text-lg font-bold">临时禁用</h2>
      <p class="mt-2 text-sm opacity-70">
        禁用后由系统在设定的时刻自动恢复，不影响其他账号。
      </p>

      <fieldset class="mt-4 fieldset">
        <label class="label">禁用范围</label>
        <label class="flex cursor-pointer items-center gap-3">
          <input
            v-model="scope"
            type="radio"
            class="radio radio-sm"
            value="account"
          />
          <span>禁用整个账号（暂停调度）</span>
        </label>
        <label class="mt-2 flex cursor-pointer items-center gap-3">
          <input
            v-model="scope"
            type="radio"
            class="radio radio-sm"
            value="model"
          />
          <span>禁用某一个模型（从账号白名单移除）</span>
        </label>
      </fieldset>

      <fieldset v-if="scope === 'model'" class="mt-4 fieldset min-w-0">
        <label class="label">要禁用的模型</label>
        <select
          v-model="model"
          class="select w-full max-w-full min-w-0 truncate"
          :disabled="loadingModels || !models.length"
        >
          <option value="" disabled>
            {{ loadingModels ? "正在读取上游模型…" : "请选择模型" }}
          </option>
          <option v-for="name in models" :key="name" :value="name">
            {{ name }}
          </option>
        </select>
        <p class="mt-2 text-xs opacity-60">
          模型列表来自 Sub2API 当前为该账号提供（或白名单允许）的模型。
        </p>
      </fieldset>

      <p v-if="message" class="mt-4 alert py-2 text-sm alert-error">
        <AppIcon name="exclamation-triangle" class="size-4" />
        <span>{{ message }}</span>
      </p>

      <div class="modal-action">
        <button
          type="button"
          class="btn"
          :disabled="saving"
          @click="closeScope"
        >
          取消
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="saving"
          @click="confirmScope"
        >
          下一步：设置时长
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop">
      <button type="button" @click="closeScope">关闭</button>
    </form>
  </dialog>

  <dialog ref="durationDialog" class="modal" @cancel.prevent="closeDuration">
    <div class="modal-box w-[calc(100vw-2rem)] max-w-md">
      <h2 class="text-lg font-bold">
        {{ editing ? "调整禁用恢复时间" : "设置禁用时长" }}
      </h2>
      <p class="mt-2 text-sm opacity-70">
        {{
          editing
            ? "新的恢复时刻从现在开始计算；也可以直接提前恢复。"
            : scope === "account"
              ? "到时由系统恢复该账号的调度。"
              : `到时由系统把模型 ${model} 放回账号白名单。`
        }}
      </p>

      <p v-if="editing" class="mt-3 text-sm">
        <span class="badge badge-outline badge-sm">
          {{
            editing.scope === "account" ? "整个账号" : `模型 ${editing.model}`
          }}
        </span>
        <span class="ml-2 text-xs opacity-60">{{ remainingLabel }}</span>
      </p>

      <fieldset class="mt-4 fieldset">
        <label class="label">时长</label>
        <div class="flex flex-wrap items-center gap-2">
          <input
            v-model.number="amount"
            type="number"
            min="1"
            step="1"
            class="input w-28 tabular-nums"
            required
          />
          <select v-model="unit" class="select w-24">
            <option value="hours">小时</option>
            <option value="minutes">分钟</option>
          </select>
        </div>
        <p class="mt-2 text-xs opacity-60">
          最长 {{ MAX_DISABLE_MINUTES / (60 * 24) }} 天；确认后立即生效。
        </p>
      </fieldset>

      <p v-if="message" class="mt-4 alert py-2 text-sm alert-error">
        <AppIcon name="exclamation-triangle" class="size-4" />
        <span>{{ message }}</span>
      </p>

      <div class="modal-action">
        <button
          type="button"
          class="btn"
          :disabled="saving"
          @click="closeDuration"
        >
          取消
        </button>
        <button
          v-if="editing"
          type="button"
          class="btn btn-error"
          :disabled="saving"
          @click="restoreNow"
        >
          提前恢复
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="!canConfirmDuration"
          @click="confirmDuration"
        >
          {{ editing ? "确认调整" : "确认禁用" }}
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop">
      <button type="button" @click="closeDuration">关闭</button>
    </form>
  </dialog>
</template>
