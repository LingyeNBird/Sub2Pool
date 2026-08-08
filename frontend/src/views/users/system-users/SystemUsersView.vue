<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api, jsonBody } from "@/services/api";
import type { Participant, SystemUser } from "@/types";

import SystemUserEditorDialog from "./components/SystemUserEditorDialog.vue";
import SystemUserStats from "./components/SystemUserStats.vue";
import SystemUserTable from "./components/SystemUserTable.vue";
import type { SystemUserEditorHandle, SystemUserFormData } from "./types";

const users = ref<SystemUser[]>([]);
const participants = ref<Participant[]>([]);
const loading = ref(true);
const saving = ref(false);
const message = ref("");
const editor = ref<SystemUserEditorHandle | null>(null);

const activeCount = computed(
  () => users.value.filter((item) => item.is_active).length,
);
const bindingCount = computed(() =>
  users.value.reduce((total, item) => total + item.participant_ids.length, 0),
);

async function load() {
  loading.value = true;
  message.value = "";
  try {
    const [userRows, participantRows] = await Promise.all([
      api<SystemUser[]>("system-users"),
      api<Participant[]>("participants"),
    ]);
    users.value = userRows;
    participants.value = participantRows;
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "加载系统用户失败";
  } finally {
    loading.value = false;
  }
}

async function save(form: SystemUserFormData, userId: number | null) {
  message.value = "";
  saving.value = true;
  try {
    await api(userId ? `system-users/${userId}` : "system-users", {
      method: userId ? "PATCH" : "POST",
      body: jsonBody(form),
    });
    editor.value?.close();
    await load();
  } catch (error) {
    editor.value?.showApiError(error);
  } finally {
    saving.value = false;
  }
}

async function remove(user: SystemUser) {
  if (!window.confirm(`确定删除系统用户“${user.username}”吗？`)) return;
  message.value = "";
  try {
    await api(`system-users/${user.id}`, { method: "DELETE" });
    await load();
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "删除用户失败";
  }
}

onMounted(load);
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">权限管理</RouterLink></li>
          <li><h1>系统用户</h1></li>
        </ul>
      </div>
    </div>
    <button class="btn btn-primary btn-sm" @click="editor?.open(null)">
      <AppIcon name="user-plus" class="size-4" />添加用户
    </button>
  </PageShellHeader>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>

  <SystemUserStats
    :user-count="users.length"
    :active-count="activeCount"
    :binding-count="bindingCount"
  />
  <SystemUserTable
    :users="users"
    :loading="loading"
    @edit="editor?.open($event)"
    @remove="remove"
  />
  <SystemUserEditorDialog
    ref="editor"
    :participants="participants"
    :saving="saving"
    @save="save"
  />
</template>
