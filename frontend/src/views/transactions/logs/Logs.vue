<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api } from "@/services/api";
import type { NotificationRecord } from "@/types";

const rows = ref<NotificationRecord[]>([]);
const loading = ref(true);
const message = ref("");
const selected = ref<NotificationRecord | null>(null);
const dialog = ref<HTMLDialogElement | null>(null);
const sentCount = computed(
  () => rows.value.filter((item) => item.status === "sent").length,
);
const failedCount = computed(
  () => rows.value.filter((item) => item.status === "failed").length,
);

function dateTime(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}

async function load() {
  loading.value = true;
  try {
    rows.value = await api<NotificationRecord[]>("notifications?limit=200");
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "加载通知记录失败";
  } finally {
    loading.value = false;
  }
}

function show(row: NotificationRecord) {
  selected.value = row;
  dialog.value?.showModal();
}

onMounted(load);
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">额度管理</RouterLink></li>
          <li><h1>通知记录</h1></li>
        </ul>
      </div>
    </div>
    <button class="btn btn-sm" @click="load">
      <AppIcon name="arrow-path" class="size-4" />
      刷新
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
        <AppIcon name="bell" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">通知记录</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ rows.length }}
      </div>
      <div class="stat-desc">包含发送、跳过和失败</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="check-circle" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">已发送</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ sentCount }}
      </div>
      <div class="stat-desc">SMTP 接收成功</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="exclamation-triangle" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">发送失败</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ failedCount }}
      </div>
      <div class="stat-desc">请检查 SMTP 设置</div>
    </div>
  </section>

  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <h2 class="card-title">
        <AppIcon name="envelope" class="size-5" />邮件审计
      </h2>
      <div v-if="loading" class="flex justify-center py-10">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>时间</th>
              <th>类型</th>
              <th>参与者</th>
              <th>主题</th>
              <th>收件人</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id">
              <td>{{ dateTime(row.created_at) }}</td>
              <td>{{ row.event_type_label }}</td>
              <td>{{ row.participant_name || "系统" }}</td>
              <td>{{ row.subject }}</td>
              <td>{{ row.recipient || "未配置" }}</td>
              <td>
                <span
                  class="badge badge-sm"
                  :class="
                    row.status === 'sent'
                      ? 'badge-success'
                      : row.status === 'failed'
                        ? 'badge-error'
                        : 'badge-warning'
                  "
                >
                  {{ row.status_label }}
                </span>
              </td>
              <td>
                <button class="btn btn-ghost btn-xs" @click="show(row)">
                  详情
                </button>
              </td>
            </tr>
            <tr v-if="rows.length === 0">
              <td colspan="7" class="py-8 text-center opacity-60">
                尚无通知记录
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>

  <dialog ref="dialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">{{ selected?.subject }}</h2>
      <div class="mt-4 text-sm leading-6 whitespace-pre-wrap">
        {{ selected?.body }}
      </div>
      <div
        v-if="selected?.error"
        class="mt-4 alert alert-soft text-sm alert-error"
      >
        {{ selected.error }}
      </div>
      <div class="modal-action">
        <button class="btn" @click="dialog?.close()">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
