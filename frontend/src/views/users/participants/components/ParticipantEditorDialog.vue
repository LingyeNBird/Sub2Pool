<script setup lang="ts">
import { reactive, ref } from "vue";

import type { Participant, Sub2APIUserOption } from "@/types";

import type { ParticipantFormData } from "../types";

const props = defineProps<{
  users: Sub2APIUserOption[];
  loadingUsers: boolean;
  userListMessage: string;
  userListError: string;
  saving: boolean;
}>();

const emit = defineEmits<{
  refreshUsers: [];
  save: [form: ParticipantFormData, participantId: number | null];
  remove: [participant: Participant];
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const editingParticipant = ref<Participant | null>(null);
const form = reactive<ParticipantFormData>({
  name: "",
  email: "",
  sub2api_user_id: 0,
  sub2api_username: "",
  sub2api_email: "",
  share_percent: 50,
  is_owner: false,
  enabled: true,
  notes: "",
});

function userRoleLabel(role: string) {
  if (role === "admin") return "管理员";
  if (role === "user") return "普通用户";
  return role || "未知角色";
}

function participantIdentity(user: {
  sub2api_username: string;
  sub2api_email: string;
  sub2api_user_id: number;
}) {
  return (
    user.sub2api_username ||
    user.sub2api_email ||
    `账号 ${user.sub2api_user_id}`
  );
}

function hasUserOption(userId: number) {
  return props.users.some((user) => user.id === userId);
}

function applySelectedUser() {
  const user = props.users.find((item) => item.id === form.sub2api_user_id);
  if (!user) return;
  form.sub2api_username = user.username;
  form.sub2api_email = user.email;
  if (editingParticipant.value) return;
  if (!form.email) form.email = user.email;
  if (!form.name) form.name = user.username || user.email || `用户 ${user.id}`;
}

function open(participant: Participant | null, defaultShare: number) {
  editingParticipant.value = participant;
  Object.assign(
    form,
    participant
      ? {
          name: participant.name,
          email: participant.email,
          sub2api_user_id: participant.sub2api_user_id,
          sub2api_username: participant.sub2api_username,
          sub2api_email: participant.sub2api_email,
          share_percent: participant.share_percent,
          is_owner: participant.is_owner,
          enabled: participant.enabled,
          notes: participant.notes,
        }
      : {
          name: "",
          email: "",
          sub2api_user_id: 0,
          sub2api_username: "",
          sub2api_email: "",
          share_percent: defaultShare,
          is_owner: defaultShare === 100,
          enabled: true,
          notes: "",
        },
  );
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
}

function submit() {
  emit("save", { ...form }, editingParticipant.value?.id ?? null);
}

function remove() {
  if (editingParticipant.value) emit("remove", editingParticipant.value);
}

defineExpose({ open, close });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">
        {{ editingParticipant ? "编辑参与者" : "添加参与者" }}
      </h2>
      <form class="mt-4 grid gap-3" @submit.prevent="submit">
        <fieldset class="fieldset">
          <label class="label">显示名称</label>
          <input v-model="form.name" class="input w-full" required />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label">邮箱（备注用）</label>
          <input v-model="form.email" type="email" class="input w-full" />
        </fieldset>
        <div class="grid gap-3">
          <fieldset class="fieldset min-w-0">
            <label class="label">Sub2API 用户</label>
            <div class="grid min-w-0 gap-2 overflow-hidden">
              <select
                v-model.number="form.sub2api_user_id"
                class="select w-full min-w-0 truncate"
                required
                @change="applySelectedUser"
              >
                <option :value="0" disabled>请选择 Sub2API 用户</option>
                <option
                  v-if="
                    form.sub2api_user_id && !hasUserOption(form.sub2api_user_id)
                  "
                  :value="form.sub2api_user_id"
                >
                  当前用户（{{ participantIdentity(form) }}）
                </option>
                <option v-for="user in users" :key="user.id" :value="user.id">
                  {{ user.username || user.email || `用户 ${user.id}` }}（ID
                  {{ user.id }} · {{ userRoleLabel(user.role) }} ·
                  {{ user.status || "未知状态" }}）
                </option>
              </select>
              <button
                type="button"
                class="btn justify-self-end btn-sm"
                :disabled="loadingUsers"
                @click="$emit('refreshUsers')"
              >
                <span
                  v-if="loadingUsers"
                  class="loading loading-xs loading-spinner"
                ></span>
                <AppIcon v-else name="arrow-path" class="size-4" />
                {{ loadingUsers ? "读取中" : "刷新用户" }}
              </button>
            </div>
            <p v-if="userListMessage" class="label text-success">
              {{ userListMessage }}
            </p>
            <p v-if="userListError" class="label text-error">
              {{ userListError }}
            </p>
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">周限权益比例（%）</label>
            <input
              v-model.number="form.share_percent"
              type="number"
              min="0"
              max="100"
              step="0.001"
              class="input w-full"
              required
            />
          </fieldset>
        </div>
        <div class="alert text-sm alert-info">
          <AppIcon name="information-circle" class="size-5" />
          <span>
            权益填写合同份额，不是当前剩余额度。例如上游已用 10%，双方仍各填
            50%；首次测算会按 Sub2API
            用户的历史用量，把已用部分归属给实际使用者。
          </span>
        </div>
        <fieldset class="fieldset">
          <label class="label">备注</label>
          <textarea v-model="form.notes" class="textarea w-full"></textarea>
        </fieldset>
        <label class="label justify-between">
          这是车主
          <input
            v-model="form.is_owner"
            type="checkbox"
            class="toggle toggle-sm"
          />
        </label>
        <label class="label justify-between">
          启用监控
          <input
            v-model="form.enabled"
            type="checkbox"
            class="toggle toggle-sm"
          />
        </label>
        <div class="modal-action">
          <button
            v-if="editingParticipant"
            type="button"
            class="btn btn-error"
            :disabled="saving"
            @click="remove"
          >
            删除
          </button>
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
