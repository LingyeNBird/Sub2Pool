<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import { ApiError, api, jsonBody } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type {
  ConfirmDialogHandle,
  Participant,
  Sub2APIUserOption,
} from "@/types";
import { formatPercent } from "@/utils/formatters";

import ParticipantCard from "./components/ParticipantCard.vue";
import ParticipantEditorDialog from "./components/ParticipantEditorDialog.vue";
import ParticipantTable from "./components/ParticipantTable.vue";
import ParticipantViewSwitcher from "./components/ParticipantViewSwitcher.vue";
import type {
  ParticipantEditorHandle,
  ParticipantFormData,
  ParticipantViewMode,
} from "./types";

const auth = useAuthStore();
const participants = ref<Participant[]>([]);
const sub2apiUsers = ref<Sub2APIUserOption[]>([]);
const loading = ref(true);
const saving = ref(false);
const loadingUsers = ref(false);
const message = ref("");
const userListMessage = ref("");
const userListError = ref("");
const editor = ref<ParticipantEditorHandle | null>(null);
const confirmDialog = ref<ConfirmDialogHandle | null>(null);

const viewModeStorageKey = "sub2pool:participant-view";
const viewMode = ref<ParticipantViewMode>("cards");
const showCards = computed(() => !auth.isStaff || viewMode.value === "cards");
const enabledCount = computed(
  () => participants.value.filter((item) => item.enabled).length,
);
const shareTotal = computed(() =>
  participants.value.reduce(
    (sum, participant) =>
      participant.enabled ? sum + participant.share_percent : sum,
    0,
  ),
);
const updateCount = computed(
  () =>
    participants.value.filter((item) => item.snapshot?.needs_manual_update)
      .length,
);

async function load() {
  loading.value = true;
  try {
    participants.value = await api<Participant[]>("participants");
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "加载参与者失败";
  } finally {
    loading.value = false;
  }
}

async function loadSub2APIUsers(showFeedback = true) {
  loadingUsers.value = true;
  if (showFeedback) {
    userListMessage.value = "";
    userListError.value = "";
  }
  try {
    const users = await api<Sub2APIUserOption[]>("participants/sub2api-users");
    sub2apiUsers.value = users;
    if (showFeedback) {
      userListMessage.value = users.length
        ? `已读取 ${users.length} 个 Sub2API 用户`
        : "Sub2API 当前没有可选择的用户";
    }
  } catch (error) {
    if (showFeedback) {
      userListError.value =
        error instanceof ApiError ? error.message : "读取 Sub2API 用户列表失败";
    }
  } finally {
    loadingUsers.value = false;
  }
}

function setViewMode(mode: ParticipantViewMode) {
  viewMode.value = mode;
  localStorage.setItem(viewModeStorageKey, mode);
}

function prepareEditor() {
  userListMessage.value = "";
  userListError.value = "";
  if (!sub2apiUsers.value.length) void loadSub2APIUsers();
}

function openNew() {
  if (!auth.isStaff) return;
  prepareEditor();
  editor.value?.open(null);
}

function openEdit(participant: Participant) {
  if (!auth.isStaff) return;
  prepareEditor();
  editor.value?.open(participant);
}

async function save(form: ParticipantFormData, participantId: number | null) {
  saving.value = true;
  message.value = "";
  try {
    await api(
      participantId ? `participants/${participantId}` : "participants",
      {
        method: participantId ? "PUT" : "POST",
        body: jsonBody(form),
      },
    );
    editor.value?.close();
    await load();
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "保存失败";
  } finally {
    saving.value = false;
  }
}

async function remove(participant: Participant) {
  if (
    !(await confirmDialog.value?.open({
      title: "删除参与者？",
      message: `确定删除“${participant.name}”吗？已有账本的参与者只能停用。`,
      confirmLabel: "删除",
      tone: "error",
    }))
  ) {
    return;
  }
  try {
    await api(`participants/${participant.id}`, { method: "DELETE" });
    editor.value?.close();
    await load();
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "删除失败";
  }
}

onMounted(() => {
  if (auth.isStaff) {
    const storedMode = localStorage.getItem(viewModeStorageKey);
    if (storedMode === "cards" || storedMode === "table") {
      viewMode.value = storedMode;
    }
    void loadSub2APIUsers(false);
  }
  void load();
});
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li>
            <RouterLink :to="auth.isStaff ? '/' : '/statistics'">
              {{ auth.isStaff ? "额度管理" : "额度统计" }}
            </RouterLink>
          </li>
          <li><h1>参与者</h1></li>
        </ul>
      </div>
    </div>
    <button v-if="auth.isStaff" class="btn btn-primary btn-sm" @click="openNew">
      <AppIcon name="plus" class="size-4" />
      添加参与者
    </button>
  </PageShellHeader>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>

  <section
    v-if="auth.isStaff"
    class="stats col-span-12 stats-vertical bg-base-200 shadow-xs xl:stats-horizontal"
  >
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">启用参与者</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ enabledCount }}
          </div>
        </div>
        <AppIcon name="users" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">混池权益合计</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ formatPercent(shareTotal) }}
          </div>
        </div>
        <AppIcon name="scale" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">建议调整</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ updateCount }}
          </div>
        </div>
        <AppIcon
          name="clipboard-document-check"
          class="size-7 shrink-0 opacity-40"
        />
      </div>
    </div>
  </section>

  <section class="col-span-12">
    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h2 class="flex items-center gap-2 text-lg font-semibold">
        <AppIcon name="user-group" class="size-5" />权益与用量
      </h2>
      <ParticipantViewSwitcher
        v-if="auth.isStaff"
        :model-value="viewMode"
        @update:model-value="setViewMode"
      />
    </div>

    <div v-if="loading" class="card bg-base-200 shadow-xs">
      <div class="card-body items-center py-10">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
    </div>
    <div
      v-else-if="participants.length && showCards"
      class="grid gap-3 xl:grid-cols-2"
    >
      <ParticipantCard
        v-for="participant in participants"
        :key="participant.id"
        :participant="participant"
        :editable="auth.isStaff"
        @edit="openEdit"
      />
    </div>
    <ParticipantTable
      v-else-if="auth.isStaff && participants.length"
      :participants="participants"
      @edit="openEdit"
      @remove="remove"
    />
    <div v-else class="card bg-base-200 shadow-xs">
      <div class="card-body py-10 text-center opacity-60">尚未添加参与者</div>
    </div>
  </section>

  <ParticipantEditorDialog
    v-if="auth.isStaff"
    ref="editor"
    :users="sub2apiUsers"
    :loading-users="loadingUsers"
    :user-list-message="userListMessage"
    :user-list-error="userListError"
    :saving="saving"
    @refresh-users="loadSub2APIUsers()"
    @save="save"
    @remove="remove"
  />
  <ConfirmDialog ref="confirmDialog" />
</template>
