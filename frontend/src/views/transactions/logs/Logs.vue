<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { useZonedDateTimeIso } from "@/composables/useDateTime";
import { ApiError, api } from "@/services/api";
import type { PaginationMeta } from "@/types/common";
import type {
  NotificationListData,
  NotificationRecord,
} from "@/types/security";

import NotificationDetailDialog from "./components/NotificationDetailDialog.vue";
import NotificationFilterDialog from "./components/NotificationFilterDialog.vue";
import NotificationStats from "./components/NotificationStats.vue";
import NotificationTable from "./components/NotificationTable.vue";
import type {
  NotificationDetailDialogHandle,
  NotificationFilterDialogHandle,
  NotificationFilterKind,
  NotificationFilterOptions,
  NotificationFilters,
} from "./types";

const toIso = useZonedDateTimeIso();
const rows = ref<NotificationRecord[]>([]);
const summary = reactive({ total: 0, sent_count: 0, failed_count: 0 });
const pagination = ref<PaginationMeta>({
  page: 1,
  page_size: 20,
  total: 0,
  total_pages: 1,
});
const filterOptions = reactive<NotificationFilterOptions>({
  types: [],
  participants: [],
  statuses: [],
});
const filters = reactive<NotificationFilters>({
  from: "",
  to: "",
  event_type: "",
  participant: "",
  subject: "",
  status: "",
});
const loading = ref(true);
const message = ref("");
const filterDialog = ref<NotificationFilterDialogHandle | null>(null);
const detailDialog = ref<NotificationDetailDialogHandle | null>(null);

function queryString() {
  const query = new URLSearchParams({
    page: String(pagination.value.page),
    page_size: String(pagination.value.page_size),
  });
  if (filters.from) query.set("from", toIso(filters.from));
  if (filters.to) query.set("to", toIso(filters.to));
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

function openFilter(kind: NotificationFilterKind) {
  filterDialog.value?.open(kind, { ...filters });
}

function applyFilters(nextFilters: NotificationFilters) {
  Object.assign(filters, nextFilters);
  pagination.value.page = 1;
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

  <NotificationStats
    :total="summary.total"
    :sent-count="summary.sent_count"
    :failed-count="summary.failed_count"
  />
  <NotificationTable
    :rows="rows"
    :filters="filters"
    :pagination="pagination"
    :loading="loading"
    @filter="openFilter"
    @detail="detailDialog?.open($event)"
    @page="changePage"
  />
  <NotificationFilterDialog
    ref="filterDialog"
    :options="filterOptions"
    @apply="applyFilters"
  />
  <NotificationDetailDialog ref="detailDialog" />
</template>
