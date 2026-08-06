<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import PaginationControls from "@/components/common/PaginationControls.vue";
import { ApiError, api } from "@/services/api";
import type {
  NotificationListData,
  NotificationRecord,
  PaginationMeta,
  SelectOption,
} from "@/types";

type FilterKind = "time" | "type" | "participant" | "subject" | "status";

const rows = ref<NotificationRecord[]>([]);
const summary = reactive({ total: 0, sent_count: 0, failed_count: 0 });
const pagination = ref<PaginationMeta>({
  page: 1,
  page_size: 20,
  total: 0,
  total_pages: 1,
});
const filterOptions = reactive<{
  types: SelectOption[];
  participants: { id: number; name: string }[];
  statuses: SelectOption[];
}>({ types: [], participants: [], statuses: [] });
const filters = reactive({
  from: "",
  to: "",
  event_type: "",
  participant: "",
  subject: "",
  status: "",
});
const draft = reactive({ ...filters });
const filterKind = ref<FilterKind>("time");
const filterDialog = ref<HTMLDialogElement | null>(null);
const loading = ref(true);
const message = ref("");
const selected = ref<NotificationRecord | null>(null);
const dialog = ref<HTMLDialogElement | null>(null);

function dateTime(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}

function queryString() {
  const query = new URLSearchParams({
    page: String(pagination.value.page),
    page_size: String(pagination.value.page_size),
  });
  if (filters.from) query.set("from", new Date(filters.from).toISOString());
  if (filters.to) query.set("to", new Date(filters.to).toISOString());
  if (filters.event_type) query.set("event_type", filters.event_type);
  if (filters.participant) query.set("participant", filters.participant);
  if (filters.subject) query.set("subject", filters.subject);
  if (filters.status) query.set("status", filters.status);
  return query.toString();
}

async function load() {
  loading.value = true;
  message.value = "";
  try {
    const result = await api<NotificationListData>(
      `notifications?${queryString()}`,
    );
    rows.value = result.items;
    pagination.value = result.pagination;
    Object.assign(summary, result.summary);
    Object.assign(filterOptions, result.filter_options);
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

function openFilter(kind: FilterKind) {
  filterKind.value = kind;
  Object.assign(draft, filters);
  filterDialog.value?.showModal();
}

function applyFilter() {
  if (filterKind.value === "time") {
    filters.from = draft.from;
    filters.to = draft.to;
  } else {
    filters[filterKind.value === "type" ? "event_type" : filterKind.value] =
      draft[filterKind.value === "type" ? "event_type" : filterKind.value];
  }
  pagination.value.page = 1;
  filterDialog.value?.close();
  void load();
}

function clearFilter() {
  if (filterKind.value === "time") {
    filters.from = "";
    filters.to = "";
  } else {
    const key = filterKind.value === "type" ? "event_type" : filterKind.value;
    filters[key] = "";
  }
  Object.assign(draft, filters);
  pagination.value.page = 1;
  filterDialog.value?.close();
  void load();
}

function changePage(page: number) {
  pagination.value.page = page;
  void load();
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
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">通知记录</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ summary.total }}
          </div>
          <div class="stat-desc">包含发送、跳过和失败</div>
        </div>
        <AppIcon name="bell" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">已发送</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ summary.sent_count }}
          </div>
          <div class="stat-desc">邮件服务接收成功</div>
        </div>
        <AppIcon name="check-circle" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">发送失败</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ summary.failed_count }}
          </div>
          <div class="stat-desc">请检查邮件服务设置</div>
        </div>
        <AppIcon
          name="exclamation-triangle"
          class="size-7 shrink-0 opacity-40"
        />
      </div>
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
      <template v-else>
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 btn-xs"
                    @click="openFilter('time')"
                  >
                    时间
                    <span v-if="filters.from || filters.to" class="text-primary"
                      >●</span
                    >
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 btn-xs"
                    @click="openFilter('type')"
                  >
                    类型
                    <span v-if="filters.event_type" class="text-primary"
                      >●</span
                    >
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 btn-xs"
                    @click="openFilter('participant')"
                  >
                    参与者
                    <span v-if="filters.participant" class="text-primary"
                      >●</span
                    >
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 btn-xs"
                    @click="openFilter('subject')"
                  >
                    主题
                    <span v-if="filters.subject" class="text-primary">●</span>
                  </button>
                </th>
                <th>收件人</th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 btn-xs"
                    @click="openFilter('status')"
                  >
                    状态
                    <span v-if="filters.status" class="text-primary">●</span>
                  </button>
                </th>
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
        <PaginationControls
          :page="pagination.page"
          :total-pages="pagination.total_pages"
          :total="pagination.total"
          @change="changePage"
        />
      </template>
    </div>
  </section>

  <dialog ref="filterDialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">
        {{
          filterKind === "time"
            ? "筛选通知时间"
            : filterKind === "type"
              ? "筛选通知类型"
              : filterKind === "participant"
                ? "筛选参与者"
                : filterKind === "subject"
                  ? "搜索主题"
                  : "筛选发送状态"
        }}
      </h2>
      <div v-if="filterKind === 'time'" class="mt-4 grid gap-3 sm:grid-cols-2">
        <fieldset class="fieldset">
          <label class="label">起始日期时间</label>
          <input
            v-model="draft.from"
            type="datetime-local"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label">终止日期时间</label>
          <input
            v-model="draft.to"
            type="datetime-local"
            class="input w-full"
          />
        </fieldset>
      </div>
      <fieldset v-else-if="filterKind === 'type'" class="mt-4 fieldset">
        <label class="label">类型</label>
        <select v-model="draft.event_type" class="select w-full">
          <option value="">全部类型</option>
          <option
            v-for="option in filterOptions.types"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>
      </fieldset>
      <fieldset v-else-if="filterKind === 'participant'" class="mt-4 fieldset">
        <label class="label">参与者</label>
        <select v-model="draft.participant" class="select w-full">
          <option value="">全部参与者</option>
          <option value="system">系统</option>
          <option
            v-for="participant in filterOptions.participants"
            :key="participant.id"
            :value="String(participant.id)"
          >
            {{ participant.name }}
          </option>
        </select>
      </fieldset>
      <fieldset v-else-if="filterKind === 'subject'" class="mt-4 fieldset">
        <label class="label">主题关键词</label>
        <input
          v-model.trim="draft.subject"
          class="input w-full"
          placeholder="输入部分主题文字"
        />
      </fieldset>
      <fieldset v-else class="mt-4 fieldset">
        <label class="label">发送状态</label>
        <select v-model="draft.status" class="select w-full">
          <option value="">全部状态</option>
          <option
            v-for="option in filterOptions.statuses"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>
      </fieldset>
      <div class="modal-action">
        <button type="button" class="btn btn-ghost" @click="clearFilter">
          清除筛选
        </button>
        <button type="button" class="btn" @click="filterDialog?.close()">
          取消
        </button>
        <button type="button" class="btn btn-primary" @click="applyFilter">
          应用
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>

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
