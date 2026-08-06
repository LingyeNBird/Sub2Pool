<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api, jsonBody } from "@/services/api";
import type { Participant } from "@/types";

const participants = ref<Participant[]>([]);
const loading = ref(true);
const saving = ref(false);
const message = ref("");
const dialog = ref<HTMLDialogElement | null>(null);
const editingId = ref<number | null>(null);
const form = reactive({
  name: "",
  email: "",
  sub2api_user_id: 0,
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

function openNew() {
  editingId.value = null;
  Object.assign(form, {
    name: "",
    email: "",
    sub2api_user_id: 0,
    share_percent: participants.value.length ? 50 : 100,
    is_owner: participants.value.length === 0,
    enabled: true,
    notes: "",
  });
  dialog.value?.showModal();
}

function openEdit(participant: Participant) {
  editingId.value = participant.id;
  Object.assign(form, {
    name: participant.name,
    email: participant.email,
    sub2api_user_id: participant.sub2api_user_id,
    share_percent: participant.share_percent,
    is_owner: participant.is_owner,
    enabled: participant.enabled,
    notes: participant.notes,
  });
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

async function remove(participant: Participant) {
  if (
    !window.confirm(
      `确定删除“${participant.name}”吗？已有账本的参与者只能停用。`,
    )
  )
    return;
  try {
    await api(`participants/${participant.id}`, { method: "DELETE" });
    await load();
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "删除失败";
  }
}

onMounted(load);
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

  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <h2 class="card-title">
        <AppIcon name="user-group" class="size-5" />权益与用量
      </h2>
      <div v-if="loading" class="flex justify-center py-10">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>参与者</th>
              <th>角色</th>
              <th>权益</th>
              <th>已归属 / 剩余</th>
              <th>Sub2API 周用量 / 限额</th>
              <th>建议限额</th>
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
                    participant.email ||
                    `用户 ID ${participant.sub2api_user_id}`
                  }}
                </div>
              </td>
              <td>
                <span
                  class="badge badge-sm"
                  :class="
                    participant.is_owner ? 'badge-neutral' : 'badge-ghost'
                  "
                  >{{ participant.is_owner ? "车主" : "车友" }}</span
                >
              </td>
              <td>{{ percent(participant.share_percent) }}</td>
              <td>
                {{ percent(participant.snapshot?.charged_cycle_percent) }} /
                {{ percent(participant.snapshot?.remaining_share_percent) }}
              </td>
              <td>
                {{ currency(participant.latest_weekly_usage_usd) }} /
                {{ currency(participant.latest_weekly_limit_usd) }}
              </td>
              <td class="font-semibold">
                {{
                  currency(participant.snapshot?.recommended_weekly_limit_usd)
                }}
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
            <tr v-if="participants.length === 0">
              <td colspan="8" class="py-8 text-center opacity-60">
                尚未添加参与者
              </td>
            </tr>
          </tbody>
        </table>
      </div>
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
            <label class="label">Sub2API 用户 ID</label>
            <input
              v-model.number="form.sub2api_user_id"
              type="number"
              min="1"
              class="input w-full"
              required
            />
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
