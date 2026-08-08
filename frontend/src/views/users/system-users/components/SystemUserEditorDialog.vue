<script setup lang="ts">
import { reactive, ref } from "vue";

import { ApiError } from "@/services/api";
import type { Participant, SystemUser } from "@/types";

import type { SystemUserFormData } from "../types";

type FieldErrorKey =
  | "username"
  | "email"
  | "password"
  | "participant_ids"
  | "non_field_errors";

const props = defineProps<{
  participants: Participant[];
  saving: boolean;
}>();

const emit = defineEmits<{
  save: [form: SystemUserFormData, userId: number | null];
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const editingUser = ref<SystemUser | null>(null);
const formMessage = ref("");
const fieldErrors = reactive<Record<FieldErrorKey, string[]>>({
  username: [],
  email: [],
  password: [],
  participant_ids: [],
  non_field_errors: [],
});
const form = reactive({
  username: "",
  email: "",
  password: "",
  is_active: true,
  participant_ids: [] as number[],
});

function clearFormErrors() {
  formMessage.value = "";
  for (const key of Object.keys(fieldErrors) as FieldErrorKey[]) {
    fieldErrors[key] = [];
  }
}

function detailMessages(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(detailMessages);
  if (value && typeof value === "object") {
    return Object.values(value).flatMap(detailMessages);
  }
  return [];
}

function applyValidationDetails(details: unknown): boolean {
  if (!details || typeof details !== "object" || Array.isArray(details)) {
    return false;
  }

  let found = false;
  const unassigned: string[] = [];
  for (const [key, value] of Object.entries(details)) {
    const errors = detailMessages(value);
    if (!errors.length) continue;
    found = true;
    if (
      key !== "non_field_errors" &&
      Object.prototype.hasOwnProperty.call(fieldErrors, key)
    ) {
      fieldErrors[key as FieldErrorKey] = errors;
    } else {
      unassigned.push(...errors);
    }
  }
  if (unassigned.length) formMessage.value = unassigned.join("；");
  return found;
}

function validateForm() {
  if (form.password && form.password.length < 10) {
    fieldErrors.password = ["密码至少需要 10 个字符"];
  }
  if (!form.participant_ids.length) {
    fieldErrors.participant_ids = ["请至少选择一个参与者"];
  }
  const valid = !Object.values(fieldErrors).some((errors) => errors.length);
  if (!valid) formMessage.value = "请检查标红的表单项";
  return valid;
}

function open(user: SystemUser | null) {
  editingUser.value = user;
  Object.assign(
    form,
    user
      ? {
          username: user.username,
          email: user.email,
          password: "",
          is_active: user.is_active,
          participant_ids: [...user.participant_ids],
        }
      : {
          username: "",
          email: "",
          password: "",
          is_active: true,
          participant_ids: [],
        },
  );
  clearFormErrors();
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
}

function submit() {
  clearFormErrors();
  if (!validateForm()) return;
  emit(
    "save",
    {
      username: form.username,
      email: form.email,
      is_active: form.is_active,
      participant_ids: [...form.participant_ids],
      ...(form.password ? { password: form.password } : {}),
    },
    editingUser.value?.id ?? null,
  );
}

function showApiError(error: unknown) {
  if (error instanceof ApiError) {
    const hasFieldErrors = applyValidationDetails(error.details);
    if (!formMessage.value) {
      formMessage.value = hasFieldErrors ? "请检查标红的表单项" : error.message;
    }
  } else {
    formMessage.value = "保存用户失败";
  }
}

defineExpose({ open, close, showApiError });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">
        {{ editingUser ? "编辑系统用户" : "添加系统用户" }}
      </h2>
      <div v-if="formMessage" class="mt-3 alert py-2 text-sm alert-error">
        <AppIcon name="exclamation-triangle" class="size-4 shrink-0" />
        <span>{{ formMessage }}</span>
      </div>
      <form class="mt-4 grid gap-3" @submit.prevent="submit">
        <fieldset class="fieldset">
          <label class="label" for="system-username">用户名</label>
          <input
            id="system-username"
            v-model="form.username"
            class="input w-full"
            maxlength="150"
            autocomplete="off"
            required
            :class="{ 'input-error': fieldErrors.username.length }"
          />
          <p
            v-for="error in fieldErrors.username"
            :key="error"
            class="mt-1 text-xs text-error"
          >
            {{ error }}
          </p>
        </fieldset>
        <fieldset class="fieldset">
          <label class="label" for="system-email">邮箱</label>
          <input
            id="system-email"
            v-model="form.email"
            type="email"
            class="input w-full"
            autocomplete="off"
            :class="{ 'input-error': fieldErrors.email.length }"
          />
          <p
            v-for="error in fieldErrors.email"
            :key="error"
            class="mt-1 text-xs text-error"
          >
            {{ error }}
          </p>
        </fieldset>
        <fieldset class="fieldset">
          <label class="label" for="system-password">
            {{ editingUser ? "新密码（留空则不修改）" : "密码" }}
          </label>
          <input
            id="system-password"
            v-model="form.password"
            type="password"
            class="input w-full"
            autocomplete="new-password"
            minlength="10"
            :class="{ 'input-error': fieldErrors.password.length }"
            :required="!editingUser"
          />
          <p class="mt-1 text-xs opacity-60">
            至少 10
            个字符；不能是常见密码、纯数字，也不能与用户名或邮箱过于相似。
          </p>
          <p
            v-for="error in fieldErrors.password"
            :key="error"
            class="mt-1 text-xs text-error"
          >
            {{ error }}
          </p>
        </fieldset>
        <fieldset class="fieldset">
          <legend class="label">可查看的参与者（至少选择一个）</legend>
          <div class="grid gap-2 rounded-box bg-base-200 p-3 sm:grid-cols-2">
            <label
              v-for="participant in props.participants"
              :key="participant.id"
              class="label cursor-pointer justify-start gap-3"
            >
              <input
                v-model="form.participant_ids"
                type="checkbox"
                class="checkbox checkbox-sm"
                :value="participant.id"
              />
              <span>
                <span class="font-medium">{{ participant.name }}</span>
                <span class="ml-1 text-xs opacity-60">
                  {{ participant.sub2api_identity }}
                </span>
              </span>
            </label>
          </div>
          <p
            v-for="error in fieldErrors.participant_ids"
            :key="error"
            class="mt-1 text-xs text-error"
          >
            {{ error }}
          </p>
        </fieldset>
        <label class="label justify-between">
          允许登录
          <input
            v-model="form.is_active"
            type="checkbox"
            class="toggle toggle-sm"
          />
        </label>
        <div class="modal-action">
          <button type="button" class="btn" @click="close">取消</button>
          <button class="btn btn-primary" :disabled="saving">
            <span
              v-if="saving"
              class="loading loading-xs loading-spinner"
            ></span>
            保存
          </button>
        </div>
      </form>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
