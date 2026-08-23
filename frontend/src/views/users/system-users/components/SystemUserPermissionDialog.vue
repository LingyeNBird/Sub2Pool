<script setup lang="ts">
import { computed, reactive, ref } from "vue";

import {
  pagePermissionCodes,
  pagePermissionGroups,
  participantScopedPagePermissions,
  type PagePermission,
} from "@/config/pagePermissions";
import { ApiError } from "@/services/api";
import type { MonitoredAccount, Participant, SystemUser } from "@/types";

import type { SystemUserPermissionFormData } from "../types";

type FieldErrorKey =
  | "page_permissions"
  | "participant_ids"
  | "account_ids"
  | "non_field_errors";

const props = defineProps<{
  participants: Participant[];
  accounts: MonitoredAccount[];
  saving: boolean;
}>();

const emit = defineEmits<{
  save: [form: SystemUserPermissionFormData, userId: number];
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const editingUser = ref<SystemUser | null>(null);
const participantQuery = ref("");
const accountQuery = ref("");
const formMessage = ref("");
const fieldErrors = reactive<Record<FieldErrorKey, string[]>>({
  page_permissions: [],
  participant_ids: [],
  account_ids: [],
  non_field_errors: [],
});
const form = reactive<{
  page_permissions: PagePermission[];
  participant_ids: number[];
  account_ids: number[];
}>({
  page_permissions: [],
  participant_ids: [],
  account_ids: [],
});

const filteredParticipants = computed(() => {
  const query = participantQuery.value.trim().toLocaleLowerCase();
  if (!query) return props.participants;
  return props.participants.filter((participant) =>
    [
      participant.name,
      participant.email,
      participant.sub2api_identity,
      participant.sub2api_username,
      participant.sub2api_email,
    ].some((value) => value.toLocaleLowerCase().includes(query)),
  );
});

const filteredAccounts = computed(() => {
  const query = accountQuery.value.trim().toLocaleLowerCase();
  if (!query) return props.accounts;
  return props.accounts.filter((account) =>
    [account.name, String(account.external_account_id)].some((value) =>
      value.toLocaleLowerCase().includes(query),
    ),
  );
});

const needsParticipantScope = computed(() =>
  form.page_permissions.some((permission) =>
    participantScopedPagePermissions.has(permission),
  ),
);

const needsAccountScope = computed(() =>
  form.page_permissions.includes("account_status"),
);

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

function open(user: SystemUser) {
  editingUser.value = user;
  form.page_permissions = [...user.page_permissions];
  form.participant_ids = [...user.participant_ids];
  form.account_ids = [...user.account_ids];
  participantQuery.value = "";
  accountQuery.value = "";
  clearFormErrors();
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
}

function selectAllPages() {
  form.page_permissions = [...pagePermissionCodes];
}

function clearPages() {
  form.page_permissions = [];
}

function selectAllParticipants() {
  form.participant_ids = props.participants.map(
    (participant) => participant.id,
  );
}

function clearParticipants() {
  form.participant_ids = [];
}

function selectAllAccounts() {
  form.account_ids = props.accounts.map((account) => account.id);
}

function clearAccounts() {
  form.account_ids = [];
}

function submit() {
  clearFormErrors();
  if (needsParticipantScope.value && !form.participant_ids.length) {
    fieldErrors.participant_ids = [
      "已开放包含参与者数据的页面，请至少选择一个可查看的参与者",
    ];
    formMessage.value = "请检查标红的表单项";
    return;
  }
  if (needsAccountScope.value && !form.account_ids.length) {
    fieldErrors.account_ids = [
      "已开放账号状态页面，请至少选择一个可查看的账号",
    ];
    formMessage.value = "请检查标红的表单项";
    return;
  }
  if (!editingUser.value) return;
  emit(
    "save",
    {
      page_permissions: [...form.page_permissions],
      participant_ids: [...form.participant_ids],
      account_ids: [...form.account_ids],
    },
    editingUser.value.id,
  );
}

function showApiError(error: unknown) {
  if (error instanceof ApiError) {
    const hasFieldErrors = applyValidationDetails(error.details);
    if (!formMessage.value) {
      formMessage.value = hasFieldErrors ? "请检查标红的表单项" : error.message;
    }
  } else {
    formMessage.value = "保存用户权限失败";
  }
}

defineExpose({ open, close, showApiError });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box max-w-4xl">
      <h2 class="text-lg font-bold">编辑“{{ editingUser?.username }}”的权限</h2>
      <p class="mt-1 text-sm opacity-60">
        页面权限决定可进入的功能；账号和参与者权限分别限制对应页面中的可见数据。
      </p>
      <div v-if="formMessage" class="mt-3 alert py-2 text-sm alert-error">
        <AppIcon name="exclamation-triangle" class="size-4 shrink-0" />
        <span>{{ formMessage }}</span>
      </div>

      <form class="mt-5 grid gap-6" @submit.prevent="submit">
        <fieldset class="fieldset">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <legend class="label font-semibold">可访问页面</legend>
            <div class="flex gap-1">
              <button
                type="button"
                class="btn btn-ghost btn-xs"
                @click="selectAllPages"
              >
                全选
              </button>
              <button
                type="button"
                class="btn btn-ghost btn-xs"
                @click="clearPages"
              >
                清空
              </button>
            </div>
          </div>
          <div class="grid gap-3 lg:grid-cols-2">
            <section
              v-for="group in pagePermissionGroups"
              :key="group.label"
              class="rounded-box border border-base-300 bg-base-100 p-3"
            >
              <h3 class="mb-2 text-sm font-semibold opacity-70">
                {{ group.label }}
              </h3>
              <div class="grid gap-1">
                <label
                  v-for="option in group.items"
                  :key="option.code"
                  class="flex cursor-pointer items-start gap-3 rounded-lg p-2 hover:bg-base-200"
                >
                  <input
                    v-model="form.page_permissions"
                    type="checkbox"
                    class="checkbox mt-0.5 checkbox-sm"
                    :value="option.code"
                  />
                  <span>
                    <span class="block text-sm font-medium">{{
                      option.label
                    }}</span>
                    <span class="block text-xs opacity-60">
                      {{ option.description }}
                    </span>
                  </span>
                </label>
              </div>
            </section>
          </div>
          <p
            v-for="error in fieldErrors.page_permissions"
            :key="error"
            class="mt-1 text-xs text-error"
          >
            {{ error }}
          </p>
        </fieldset>

        <fieldset class="fieldset">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <legend class="label font-semibold">可查看的账号</legend>
            <div class="flex gap-1">
              <button
                type="button"
                class="btn btn-ghost btn-xs"
                @click="selectAllAccounts"
              >
                全选
              </button>
              <button
                type="button"
                class="btn btn-ghost btn-xs"
                @click="clearAccounts"
              >
                清空
              </button>
            </div>
          </div>
          <p class="mb-2 text-xs opacity-60">
            账号状态页面只会显示这里勾选的上游账号。
          </p>
          <label class="input mb-2 flex w-full items-center gap-2 input-sm">
            <AppIcon name="magnifying-glass" class="size-4 opacity-50" />
            <input
              v-model="accountQuery"
              type="search"
              class="grow"
              placeholder="搜索账号名称或 Sub2API 账号 ID"
            />
          </label>
          <div
            class="grid max-h-52 gap-1 overflow-y-auto rounded-box border border-base-300 bg-base-100 p-2 sm:grid-cols-2"
            :class="{ 'border-error': fieldErrors.account_ids.length }"
          >
            <label
              v-for="account in filteredAccounts"
              :key="account.id"
              class="flex cursor-pointer items-start gap-3 rounded-lg p-2 hover:bg-base-200"
            >
              <input
                v-model="form.account_ids"
                type="checkbox"
                class="checkbox mt-0.5 checkbox-sm"
                :value="account.id"
              />
              <span class="min-w-0">
                <span class="block truncate text-sm font-medium">
                  {{ account.name }}
                </span>
                <span class="block truncate text-xs opacity-60">
                  Sub2API #{{ account.external_account_id }}
                </span>
              </span>
            </label>
            <p
              v-if="!filteredAccounts.length"
              class="col-span-full py-4 text-center text-sm opacity-50"
            >
              没有匹配的账号
            </p>
          </div>
          <p
            v-for="error in fieldErrors.account_ids"
            :key="error"
            class="mt-1 text-xs text-error"
          >
            {{ error }}
          </p>
        </fieldset>

        <fieldset class="fieldset">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <legend class="label font-semibold">可查看的参与者</legend>
            <div class="flex gap-1">
              <button
                type="button"
                class="btn btn-ghost btn-xs"
                @click="selectAllParticipants"
              >
                全选
              </button>
              <button
                type="button"
                class="btn btn-ghost btn-xs"
                @click="clearParticipants"
              >
                清空
              </button>
            </div>
          </div>
          <p class="mb-2 text-xs opacity-60">
            额度总览、参与者、观测记录、额度统计和通知记录会按这里的选择过滤。
          </p>
          <label class="input mb-2 flex w-full items-center gap-2 input-sm">
            <AppIcon name="magnifying-glass" class="size-4 opacity-50" />
            <input
              v-model="participantQuery"
              type="search"
              class="grow"
              placeholder="搜索姓名、邮箱或 Sub2API 身份"
            />
          </label>
          <div
            class="grid max-h-64 gap-1 overflow-y-auto rounded-box border border-base-300 bg-base-100 p-2 sm:grid-cols-2"
            :class="{ 'border-error': fieldErrors.participant_ids.length }"
          >
            <label
              v-for="participant in filteredParticipants"
              :key="participant.id"
              class="flex cursor-pointer items-start gap-3 rounded-lg p-2 hover:bg-base-200"
            >
              <input
                v-model="form.participant_ids"
                type="checkbox"
                class="checkbox mt-0.5 checkbox-sm"
                :value="participant.id"
              />
              <span class="min-w-0">
                <span class="block truncate text-sm font-medium">
                  {{ participant.name }}
                </span>
                <span class="block truncate text-xs opacity-60">
                  {{ participant.sub2api_identity }}
                </span>
              </span>
            </label>
            <p
              v-if="!filteredParticipants.length"
              class="col-span-full py-4 text-center text-sm opacity-50"
            >
              没有匹配的参与者
            </p>
          </div>
          <p
            v-for="error in fieldErrors.participant_ids"
            :key="error"
            class="mt-1 text-xs text-error"
          >
            {{ error }}
          </p>
        </fieldset>

        <div class="modal-action">
          <button type="button" class="btn" @click="close">取消</button>
          <button class="btn btn-primary" :disabled="saving">
            <span
              v-if="saving"
              class="loading loading-xs loading-spinner"
            ></span>
            保存权限
          </button>
        </div>
      </form>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
