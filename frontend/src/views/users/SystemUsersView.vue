<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api, jsonBody } from "@/services/api";
import type { Participant, SystemUser } from "@/types";

const users = ref<SystemUser[]>([]);
const participants = ref<Participant[]>([]);
const loading = ref(true);
const saving = ref(false);
const message = ref("");
const dialog = ref<HTMLDialogElement | null>(null);
const editingId = ref<number | null>(null);
const form = reactive({
  username: "",
  email: "",
  password: "",
  is_active: true,
  participant_ids: [] as number[],
});

const activeCount = computed(
  () => users.value.filter((item) => item.is_active).length,
);
const bindingCount = computed(() =>
  users.value.reduce((total, item) => total + item.participant_ids.length, 0),
);

function dateTime(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "从未登录";
}

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

function openNew() {
  editingId.value = null;
  Object.assign(form, {
    username: "",
    email: "",
    password: "",
    is_active: true,
    participant_ids: [],
  });
  dialog.value?.showModal();
}

function openEdit(user: SystemUser) {
  editingId.value = user.id;
  Object.assign(form, {
    username: user.username,
    email: user.email,
    password: "",
    is_active: user.is_active,
    participant_ids: [...user.participant_ids],
  });
  dialog.value?.showModal();
}

async function save() {
  saving.value = true;
  message.value = "";
  const payload = {
    username: form.username,
    email: form.email,
    is_active: form.is_active,
    participant_ids: form.participant_ids,
    ...(form.password ? { password: form.password } : {}),
  };
  try {
    await api(
      editingId.value ? `system-users/${editingId.value}` : "system-users",
      {
        method: editingId.value ? "PATCH" : "POST",
        body: jsonBody(payload),
      },
    );
    dialog.value?.close();
    await load();
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "保存用户失败";
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
    <button class="btn btn-primary btn-sm" @click="openNew">
      <AppIcon name="user-plus" class="size-4" />添加用户
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
      <div class="stat-title">普通用户</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ users.length }}
      </div>
      <div class="stat-desc">不包含管理员账号</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="check-circle" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">已启用</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ activeCount }}
      </div>
      <div class="stat-desc">停用后不能继续登录</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="link" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">参与者绑定</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ bindingCount }}
      </div>
      <div class="stat-desc">一个用户可以绑定多个参与者</div>
    </div>
  </section>

  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <div>
        <h2 class="card-title">
          <AppIcon name="identification" class="size-5" />用户与可见范围
        </h2>
        <p class="mt-1 text-sm opacity-60">
          普通用户只能进入额度统计页面，并且只能看到绑定参与者的账号用量。
        </p>
      </div>
      <div v-if="loading" class="flex justify-center py-10">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div v-else-if="users.length" class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>用户</th>
              <th>绑定参与者</th>
              <th>状态</th>
              <th>最近登录</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in users" :key="user.id">
              <td>
                <div class="font-semibold">{{ user.username }}</div>
                <div class="text-sm opacity-60">
                  {{ user.email || "未填写邮箱" }}
                </div>
              </td>
              <td>
                <div class="flex max-w-lg flex-wrap gap-1">
                  <span
                    v-for="(name, index) in user.participant_names"
                    :key="`${user.id}-${index}`"
                    class="badge badge-ghost badge-sm"
                  >
                    {{ name }}
                  </span>
                </div>
              </td>
              <td>
                <span
                  class="badge badge-sm"
                  :class="user.is_active ? 'badge-success' : 'badge-ghost'"
                >
                  {{ user.is_active ? "启用" : "停用" }}
                </span>
              </td>
              <td class="whitespace-nowrap">{{ dateTime(user.last_login) }}</td>
              <td class="text-right whitespace-nowrap">
                <button class="btn btn-ghost btn-xs" @click="openEdit(user)">
                  编辑
                </button>
                <button
                  class="btn btn-ghost text-error btn-xs"
                  @click="remove(user)"
                >
                  删除
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="py-8 text-center opacity-60">尚未添加普通用户</div>
    </div>
  </section>

  <dialog ref="dialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">
        {{ editingId ? "编辑系统用户" : "添加系统用户" }}
      </h2>
      <form class="mt-4 grid gap-3" @submit.prevent="save">
        <fieldset class="fieldset">
          <label class="label" for="system-username">用户名</label>
          <input
            id="system-username"
            v-model="form.username"
            class="input w-full"
            maxlength="150"
            autocomplete="off"
            required
          />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label" for="system-email">邮箱</label>
          <input
            id="system-email"
            v-model="form.email"
            type="email"
            class="input w-full"
            autocomplete="off"
          />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label" for="system-password">
            {{ editingId ? "新密码（留空则不修改）" : "密码" }}
          </label>
          <input
            id="system-password"
            v-model="form.password"
            type="password"
            class="input w-full"
            autocomplete="new-password"
            :required="!editingId"
          />
        </fieldset>
        <fieldset class="fieldset">
          <legend class="label">可查看的参与者（至少选择一个）</legend>
          <div class="grid gap-2 rounded-box bg-base-200 p-3 sm:grid-cols-2">
            <label
              v-for="participant in participants"
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
                  {{ participant.sub2api_username }}
                </span>
              </span>
            </label>
          </div>
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
          <button type="button" class="btn" @click="dialog?.close()">
            取消
          </button>
          <button
            class="btn btn-primary"
            :disabled="saving || !form.participant_ids.length"
          >
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
