<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api, jsonBody } from "@/services/api";
import type { Participant, Sub2APIUserOption } from "@/types";

const participants = ref<Participant[]>([]);
const sub2apiUsers = ref<Sub2APIUserOption[]>([]);
const loading = ref(true);
const saving = ref(false);
const loadingUsers = ref(false);
const message = ref("");
const userListMessage = ref("");
const userListError = ref("");
const dialog = ref<HTMLDialogElement | null>(null);
const editingId = ref<number | null>(null);
const pressedCardId = ref<number | null>(null);
type ParticipantViewMode = "cards" | "table";

const viewModeStorageKey = "sub2pool:participant-view";
const viewMode = ref<ParticipantViewMode>("cards");

function setViewMode(mode: ParticipantViewMode) {
  viewMode.value = mode;
  localStorage.setItem(viewModeStorageKey, mode);
}
const form = reactive({
  name: "",
  email: "",
  sub2api_user_id: 0,
  sub2api_username: "",
  share_percent: 50,
  is_owner: false,
  enabled: true,
  notes: "",
});

const enabledCount = computed(
  () => participants.value.filter((item) => item.enabled).length,
);
const shareTotal = computed(() =>
  participants.value
    .filter((item) => item.enabled)
    .reduce((sum, item) => sum + item.share_percent, 0),
);
const updateCount = computed(
  () =>
    participants.value.filter((item) => item.snapshot?.needs_manual_update)
      .length,
);

function currency(value: number | null | undefined) {
  return value == null ? "—" : `$${value.toFixed(2)}`;
}

function percent(value: number | null | undefined) {
  return value == null ? "—" : `${value.toFixed(2)}%`;
}

function compactPercent(value: number | null | undefined) {
  if (value == null) return "—";
  return `${Number(value.toFixed(2))}%`;
}

function remainingShare(participant: Participant) {
  return (
    participant.snapshot?.remaining_share_percent ?? participant.share_percent
  );
}

function allocationSegmentWidth(participant: Participant, value: number) {
  const used = participant.snapshot?.charged_cycle_percent ?? 0;
  const remaining = remainingShare(participant);
  const total = Math.max(used, 0) + Math.max(remaining, 0);
  if (total === 0) return 0;
  return (Math.max(value, 0) / total) * 100;
}

function dateTime(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}

function userRoleLabel(role: string) {
  if (role === "admin") return "管理员";
  if (role === "user") return "普通用户";
  return role || "未知角色";
}

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

function hasUserOption(userId: number) {
  return sub2apiUsers.value.some((user) => user.id === userId);
}

function applySelectedUser() {
  const user = sub2apiUsers.value.find(
    (item) => item.id === form.sub2api_user_id,
  );
  if (!user) return;
  form.sub2api_username =
    user.username || user.email.split("@")[0] || `用户 ${user.id}`;
  if (editingId.value) return;
  if (!form.email) form.email = user.email;
  if (!form.name) form.name = form.sub2api_username;
}

function openNew() {
  userListMessage.value = "";
  userListError.value = "";
  editingId.value = null;
  Object.assign(form, {
    name: "",
    email: "",
    sub2api_user_id: 0,
    sub2api_username: "",
    share_percent: participants.value.length ? 50 : 100,
    is_owner: participants.value.length === 0,
    enabled: true,
    notes: "",
  });
  if (!sub2apiUsers.value.length) void loadSub2APIUsers();
  dialog.value?.showModal();
}

function openEdit(participant: Participant) {
  userListMessage.value = "";
  userListError.value = "";
  editingId.value = participant.id;
  Object.assign(form, {
    name: participant.name,
    email: participant.email,
    sub2api_user_id: participant.sub2api_user_id,
    sub2api_username: participant.sub2api_username,
    share_percent: participant.share_percent,
    is_owner: participant.is_owner,
    enabled: participant.enabled,
    notes: participant.notes,
  });
  if (!sub2apiUsers.value.length) void loadSub2APIUsers();
  dialog.value?.showModal();
}

async function save() {
  saving.value = true;
  message.value = "";
  const path = editingId.value
    ? `participants/${editingId.value}`
    : "participants";
  try {
    await api(path, {
      method: editingId.value ? "PUT" : "POST",
      body: jsonBody(form),
    });
    dialog.value?.close();
    await load();
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "保存失败";
  } finally {
    saving.value = false;
  }
}

async function remove(participant: Participant): Promise<boolean> {
  if (
    !window.confirm(
      `确定删除“${participant.name}”吗？已有账本的参与者只能停用。`,
    )
  ) {
    return false;
  }
  try {
    await api(`participants/${participant.id}`, { method: "DELETE" });
    await load();
    return true;
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "删除失败";
    return false;
  }
}

async function removeEditingParticipant() {
  const participant = participants.value.find(
    (item) => item.id === editingId.value,
  );
  if (participant && (await remove(participant))) {
    dialog.value?.close();
  }
}

onMounted(() => {
  const storedMode = localStorage.getItem(viewModeStorageKey);
  if (storedMode === "cards" || storedMode === "table") {
    viewMode.value = storedMode;
  }
  void load();
  void loadSub2APIUsers(false);
});
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">额度管理</RouterLink></li>
          <li><h1>参与者</h1></li>
        </ul>
      </div>
    </div>
    <button class="btn btn-primary btn-sm" @click="openNew">
      <AppIcon name="plus" class="size-4" />
      添加参与者
    </button>
  </PageShellHeader>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>

  <section
    class="stats col-span-12 stats-vertical bg-base-200 shadow-xs xl:stats-horizontal"
  >
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="users" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">启用参与者</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ enabledCount }}
      </div>
      <div class="stat-desc">共 {{ participants.length }} 条记录</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="scale" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">权益比例合计</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ percent(shareTotal) }}
      </div>
      <div class="stat-desc">启用记录不能超过 100%</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="clipboard-document-check" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">建议调整</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ updateCount }}
      </div>
      <div class="stat-desc">需在 Sub2API 手动操作</div>
    </div>
  </section>

  <section class="col-span-12">
    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h2 class="flex items-center gap-2 text-lg font-semibold">
        <AppIcon name="user-group" class="size-5" />权益与用量
      </h2>
      <div class="join">
        <button
          type="button"
          class="btn join-item btn-sm"
          :class="{ 'btn-active': viewMode === 'cards' }"
          @click="setViewMode('cards')"
        >
          <AppIcon name="squares-2x2" class="size-4" />
          卡片
        </button>
        <button
          type="button"
          class="btn join-item btn-sm"
          :class="{ 'btn-active': viewMode === 'table' }"
          @click="setViewMode('table')"
        >
          <AppIcon name="table-cells" class="size-4" />
          表格
        </button>
      </div>
    </div>

    <div v-if="loading" class="card bg-base-200 shadow-xs">
      <div class="card-body items-center py-10">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
    </div>

    <div
      v-else-if="participants.length && viewMode === 'cards'"
      class="grid gap-3 xl:grid-cols-2"
    >
      <div
        v-for="participant in participants"
        :key="participant.id"
        class="relative min-w-0 p-3"
      >
        <div
          class="w-full cursor-pointer transition-transform duration-150 select-none"
          :class="{ 'scale-95': pressedCardId === participant.id }"
          role="button"
          tabindex="0"
          :aria-label="`编辑参与者 ${participant.name}`"
          @click="openEdit(participant)"
          @keydown.enter.prevent="openEdit(participant)"
          @keydown.space.prevent="openEdit(participant)"
          @pointerdown="pressedCardId = participant.id"
          @pointerup="pressedCardId = null"
          @pointercancel="pressedCardId = null"
          @pointerleave="pressedCardId = null"
        >
          <div class="hover-3d w-full">
            <article class="card w-full bg-base-200 shadow-xs">
              <div class="card-body gap-4">
                <div>
                  <div class="flex flex-wrap items-center gap-2">
                    <h3 class="card-title">{{ participant.name }}</h3>
                    <span
                      class="badge badge-sm"
                      :class="
                        participant.is_owner ? 'badge-neutral' : 'badge-ghost'
                      "
                    >
                      {{ participant.is_owner ? "车主" : "车友" }}
                    </span>
                    <span
                      class="badge badge-sm"
                      :class="
                        participant.enabled ? 'badge-success' : 'badge-ghost'
                      "
                    >
                      {{ participant.enabled ? "启用" : "停用" }}
                    </span>
                  </div>
                  <p class="mt-1 text-sm opacity-60">
                    {{ participant.email || "未填写邮箱" }} · Sub2API 账号
                    <span class="font-medium">{{
                      participant.sub2api_username
                    }}</span>
                  </p>
                </div>

                <div
                  class="rounded-box bg-base-100 p-3 sm:flex sm:items-center sm:gap-5"
                >
                  <div class="mb-3 shrink-0 sm:mb-0 sm:min-w-24">
                    <div class="text-xs opacity-60">合同权益</div>
                    <div class="mt-1 text-xl font-semibold tabular-nums">
                      {{ percent(participant.share_percent) }}
                    </div>
                  </div>
                  <div
                    class="flex h-8 min-w-0 grow overflow-hidden rounded-box bg-base-300 text-xs font-semibold tabular-nums"
                    role="img"
                    :aria-label="`已归属 ${percent(participant.snapshot?.charged_cycle_percent)}, 剩余 ${percent(remainingShare(participant))}`"
                  >
                    <div
                      v-if="
                        (participant.snapshot?.charged_cycle_percent ?? 0) > 0
                      "
                      class="flex items-center justify-center overflow-hidden bg-warning px-1 text-warning-content"
                      :style="{
                        width: `${allocationSegmentWidth(
                          participant,
                          participant.snapshot?.charged_cycle_percent ?? 0,
                        )}%`,
                      }"
                    >
                      <span class="text-[10px] whitespace-nowrap sm:text-xs">
                        已用
                        {{
                          compactPercent(
                            participant.snapshot?.charged_cycle_percent,
                          )
                        }}
                      </span>
                    </div>
                    <div
                      v-if="remainingShare(participant) > 0"
                      class="flex items-center justify-center overflow-hidden bg-primary px-1 text-primary-content"
                      :style="{
                        width: `${allocationSegmentWidth(
                          participant,
                          remainingShare(participant),
                        )}%`,
                      }"
                    >
                      <span class="text-[10px] whitespace-nowrap sm:text-xs">
                        剩余 {{ compactPercent(remainingShare(participant)) }}
                      </span>
                    </div>
                    <div
                      v-if="
                        (participant.snapshot?.charged_cycle_percent ?? 0) <=
                          0 && remainingShare(participant) <= 0
                      "
                      class="flex grow items-center justify-center opacity-60"
                    >
                      暂无可分配权益
                    </div>
                  </div>
                </div>

                <div class="grid gap-3 sm:grid-cols-2">
                  <div class="rounded-box bg-base-100 p-3">
                    <div class="text-xs opacity-60">账号本周期用量</div>
                    <div class="mt-1 font-semibold tabular-nums">
                      {{ currency(participant.latest_selected_cost) }}
                    </div>
                  </div>
                  <div class="rounded-box bg-base-100 p-3">
                    <div class="text-xs opacity-60">余额</div>
                    <div class="mt-1 font-semibold tabular-nums">
                      当前 {{ currency(participant.latest_balance_usd) }} / 建议
                      {{
                        currency(participant.snapshot?.recommended_balance_usd)
                      }}
                    </div>
                  </div>
                </div>

                <div
                  class="grid gap-3"
                  :class="{ 'sm:grid-cols-2': participant.notes }"
                >
                  <div v-if="participant.notes">
                    <div class="text-xs opacity-60">备注</div>
                    <p class="mt-1 text-sm whitespace-pre-wrap">
                      {{ participant.notes }}
                    </p>
                  </div>
                  <div>
                    <div class="text-xs opacity-60">额度建议</div>
                    <div class="mt-1 flex flex-wrap items-center gap-2">
                      <span
                        class="badge badge-sm"
                        :class="
                          participant.snapshot?.needs_manual_update
                            ? 'badge-warning'
                            : 'badge-success'
                        "
                      >
                        {{
                          !participant.snapshot
                            ? "等待测算"
                            : participant.snapshot.needs_manual_update
                              ? "建议调整"
                              : "无需调整"
                        }}
                      </span>
                      <span class="text-sm opacity-60">
                        {{ participant.snapshot?.reason || "尚无测算依据" }}
                      </span>
                    </div>
                  </div>
                </div>

                <div class="text-xs opacity-50">
                  最近探测：{{ dateTime(participant.last_checked_at) }}
                </div>
              </div>
            </article>
            <div></div>
            <div></div>
            <div></div>
            <div></div>
            <div></div>
            <div></div>
            <div></div>
            <div></div>
          </div>
        </div>
      </div>
    </div>

    <div
      v-else-if="participants.length"
      class="overflow-x-auto rounded-box bg-base-200 shadow-xs"
    >
      <table class="table">
        <thead>
          <tr>
            <th>参与者</th>
            <th>角色</th>
            <th>权益</th>
            <th>已归属 / 剩余</th>
            <th>账号本周期用量</th>
            <th>当前用户余额</th>
            <th>建议用户余额</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="participant in participants" :key="participant.id">
            <td>
              <div class="font-bold">{{ participant.name }}</div>
              <div class="text-sm opacity-60">
                {{
                  participant.email || `用户 ID ${participant.sub2api_user_id}`
                }}
              </div>
            </td>
            <td>
              <span
                class="badge badge-sm"
                :class="participant.is_owner ? 'badge-neutral' : 'badge-ghost'"
                >{{ participant.is_owner ? "车主" : "车友" }}</span
              >
            </td>
            <td>{{ percent(participant.share_percent) }}</td>
            <td>
              {{ percent(participant.snapshot?.charged_cycle_percent) }} /
              {{ percent(participant.snapshot?.remaining_share_percent) }}
            </td>
            <td>{{ currency(participant.latest_selected_cost) }}</td>
            <td>{{ currency(participant.latest_balance_usd) }}</td>
            <td class="font-semibold">
              {{ currency(participant.snapshot?.recommended_balance_usd) }}
            </td>
            <td>
              <span
                class="badge badge-sm"
                :class="participant.enabled ? 'badge-success' : 'badge-ghost'"
              >
                {{ participant.enabled ? "启用" : "停用" }}
              </span>
            </td>
            <td class="text-right">
              <button
                class="btn btn-ghost btn-xs"
                @click="openEdit(participant)"
              >
                编辑
              </button>
              <button
                class="btn btn-ghost text-error btn-xs"
                @click="remove(participant)"
              >
                删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="card bg-base-200 shadow-xs">
      <div class="card-body py-10 text-center opacity-60">尚未添加参与者</div>
    </div>
  </section>

  <dialog ref="dialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">
        {{ editingId ? "编辑参与者" : "添加参与者" }}
      </h2>
      <form class="mt-4 grid gap-3" @submit.prevent="save">
        <fieldset class="fieldset">
          <label class="label">显示名称</label>
          <input v-model="form.name" class="input w-full" required />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label">邮箱（备注用）</label>
          <input v-model="form.email" type="email" class="input w-full" />
        </fieldset>
        <div class="grid gap-3 md:grid-cols-2">
          <fieldset class="fieldset">
            <label class="label">Sub2API 用户</label>
            <div class="join w-full">
              <select
                v-model.number="form.sub2api_user_id"
                class="select join-item grow"
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
                  当前用户（{{
                    form.sub2api_username || `ID ${form.sub2api_user_id}`
                  }}）
                </option>
                <option
                  v-for="user in sub2apiUsers"
                  :key="user.id"
                  :value="user.id"
                >
                  {{ user.username || user.email || `用户 ${user.id}` }}（ID
                  {{ user.id }} · {{ userRoleLabel(user.role) }} ·
                  {{ user.status || "未知状态" }}）
                </option>
              </select>
              <button
                type="button"
                class="btn join-item"
                :disabled="loadingUsers"
                @click="loadSub2APIUsers()"
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
            v-if="editingId"
            type="button"
            class="btn btn-error"
            :disabled="saving"
            @click="removeEditingParticipant"
          >
            删除
          </button>
          <button type="button" class="btn" @click="dialog?.close()">
            取消
          </button>
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
