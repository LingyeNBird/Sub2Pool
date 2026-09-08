<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import CPAKeyManager from "./components/CPAKeyManager.vue";
import CPAPoolCard from "@/components/common/CPAPoolCard.vue";
import type { CPAPoolSummary } from "@/types/cpa";
import type { MonitoredAccount } from "@/types/accounts";
import PageShellHeader from "@/components/common/PageShellHeader.vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import { ApiError, api, jsonBody } from "@/services/api";
import { useRoute } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import type { ConfirmDialogHandle } from "@/types/common";
import type { Participant, Sub2APIUserOption } from "@/types/participants";

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
const provider = ref<"sub2api" | "cpa">(
  useRoute().query.provider === "cpa" ? "cpa" : "sub2api",
);
const cpaAccounts = ref<MonitoredAccount[]>([]);
const cpaAccountId = ref<number | null>(null);
const cpaSummary = ref<CPAPoolSummary | null>(null);
let cpaGeneration = 0;
async function loadCPA() {
  const current = ++cpaGeneration;
  cpaSummary.value = null;
  if (cpaAccountId.value == null) return;
  try {
    const result = await api<CPAPoolSummary>(
      `cpa/summary?account_id=${cpaAccountId.value}`,
    );
    if (current === cpaGeneration) cpaSummary.value = result;
  } catch (error) {
    if (current === cpaGeneration)
      message.value =
        error instanceof ApiError ? error.message : "加载 CPA 额度失败";
  }
}
watch(cpaAccountId, loadCPA);

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
const allocationCount = computed(() =>
  participants.value.reduce(
    (sum, participant) => sum + participant.pool_allocations.length,
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
    cpaAccounts.value = (
      await api<MonitoredAccount[]>("settings/monitored-accounts")
    ).filter((a) => a.provider === "cpa" && a.enabled);
    if (cpaAccountId.value == null)
      cpaAccountId.value = cpaAccounts.value[0]?.id ?? null;
    if (
      !participants.value.some((p) => p.sub2api_user_id != null) &&
      cpaAccounts.value.length
    )
      provider.value = "cpa";
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
  if (provider.value === "sub2api" && !sub2apiUsers.value.length)
    void loadSub2APIUsers();
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
    if (provider.value === "cpa") await loadCPA();
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
    if (provider.value === "cpa") await loadCPA();
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
    <select
      v-model="provider"
      class="select select-sm"
      aria-label="选择参与者渠道"
    >
      <option value="sub2api">Sub2API</option>
      <option value="cpa">CPA</option>
    </select>
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
    v-if="auth.isStaff && provider === 'sub2api'"
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
          <div class="stat-title">参与池关系</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ allocationCount }}
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

  <section v-if="provider === 'sub2api'" class="col-span-12">
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

  <template v-if="provider === 'cpa'">
    <CPAKeyManager v-if="auth.isStaff" :participants="participants" />
    <div class="col-span-12 flex flex-wrap gap-2">
      <select
        v-if="cpaAccounts.length"
        v-model="cpaAccountId"
        class="select"
        aria-label="选择 CPA 账号"
      >
        <option
          v-for="account in cpaAccounts"
          :key="account.id"
          :value="account.id"
        >
          {{ account.name }}
        </option>
      </select>
      <button class="btn" @click="loadCPA">刷新 CPA 额度</button>
    </div>
    <CPAPoolCard v-if="cpaSummary" :data="cpaSummary" @refresh="loadCPA" />
    <p v-else class="col-span-12">
      {{
        cpaAccounts.length
          ? "等待 CPA 额度数据"
          : "尚无可查看的 CPA 账号，请配置账号、额度池和成员授权。"
      }}
    </p>
    <section
      v-if="auth.isStaff && participants.length"
      class="card col-span-12 bg-base-200"
    >
      <div class="card-body">
        <h2 class="card-title">参与者身份与状态</h2>
        <p class="text-sm opacity-60">
          在系统用户页面为登录账号授权参与者及 CPA 账号。
        </p>
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>参与者</th>
                <th>渠道身份</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="participant in participants" :key="participant.id">
                <td>{{ participant.name }}</td>
                <td>
                  {{
                    participant.sub2api_user_id == null
                      ? "可绑定 CPA Key"
                      : `同时绑定 Sub2API 用户 ${participant.sub2api_user_id}`
                  }}
                </td>
                <td>{{ participant.enabled ? "启用" : "停用" }}</td>
                <td>
                  <button class="btn btn-sm" @click="openEdit(participant)">
                    编辑
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </template>
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
