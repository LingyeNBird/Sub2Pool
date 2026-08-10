<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import { useZonedDateTimeIso } from "@/composables/useDateTime";
import { ApiError, api } from "@/services/api";
import type {
  ConfirmDialogHandle,
  MonitorSchedule,
  Observation,
  ObservationRebuildResult,
  ObservationListData,
  PaginationMeta,
} from "@/types";

import CostDeltaDetailDialog from "./components/CostDeltaDetailDialog.vue";
import FastCorrectionDetailDialog from "./components/FastCorrectionDetailDialog.vue";
import ExcludeObservationDialog from "./components/ExcludeObservationDialog.vue";
import ManualStartDialog from "./components/ManualStartDialog.vue";
import ObservationDetailDialog from "./components/ObservationDetailDialog.vue";
import ObservationFilterDialog from "./components/ObservationFilterDialog.vue";
import ObservationStats from "./components/ObservationStats.vue";
import ObservationTable from "./components/ObservationTable.vue";
import { useMonitorScheduleCountdown } from "./composables/useMonitorScheduleCountdown";
import type {
  ObservationFilterKind,
  ObservationFilters,
  ObservationSummary,
} from "./types";

const toIso = useZonedDateTimeIso();
const rows = ref<Observation[]>([]);
const summary = reactive<ObservationSummary>({
  total: 0,
  valid_count: 0,
  passive_count: 0,
  excluded_count: 0,
});
const pagination = ref<PaginationMeta>({
  page: 1,
  page_size: 20,
  total: 0,
  total_pages: 1,
});
const filters = reactive<ObservationFilters>({
  from: "",
  to: "",
  source: "",
  query_mode: "",
});
const loading = ref(true);
const running = ref(false);
const message = ref("");
const excluding = ref(false);
const restoringId = ref<number | null>(null);
const manualStartId = ref<number | null>(null);
const rebuilding = ref(false);
const success = ref("");
const fastCorrectionEnabled = ref(true);

const filterDialog = ref<InstanceType<typeof ObservationFilterDialog> | null>(
  null,
);
const costDetailDialog = ref<InstanceType<typeof CostDeltaDetailDialog> | null>(
  null,
);
const fastCorrectionDetailDialog = ref<InstanceType<
  typeof FastCorrectionDetailDialog
> | null>(null);
const detailDialog = ref<InstanceType<typeof ObservationDetailDialog> | null>(
  null,
);
const excludeDialog = ref<InstanceType<typeof ExcludeObservationDialog> | null>(
  null,
);
const manualStartDialog = ref<InstanceType<typeof ManualStartDialog> | null>(
  null,
);
const confirmDialog = ref<ConfirmDialogHandle | null>(null);

const { schedule, countdownProgress, remainingLabel, applySchedule } =
  useMonitorScheduleCountdown(() => load());

function queryString() {
  const query = new URLSearchParams({
    page: String(pagination.value.page),
    page_size: String(pagination.value.page_size),
  });
  if (filters.from) query.set("from", toIso(filters.from));
  if (filters.to) query.set("to", toIso(filters.to));
  if (filters.source) query.set("source", filters.source);
  if (filters.query_mode) query.set("query_mode", filters.query_mode);
  return query.toString();
}

async function load() {
  loading.value = true;
  message.value = "";
  try {
    const [observations, monitorSchedule] = await Promise.all([
      api<ObservationListData>(`observations?${queryString()}`),
      api<MonitorSchedule>("monitor/run"),
    ]);
    rows.value = observations.items;
    pagination.value = observations.pagination;
    Object.assign(summary, observations.summary);
    fastCorrectionEnabled.value = observations.fast_correction_enabled;
    applySchedule(monitorSchedule);
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "加载观测记录失败";
  } finally {
    loading.value = false;
  }
}

async function run() {
  running.value = true;
  message.value = "";
  try {
    await api("monitor/run", { method: "POST" });
    pagination.value.page = 1;
    await load();
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "测算失败";
  } finally {
    running.value = false;
  }
}

async function confirmExclude(row: Observation, reason: string) {
  excluding.value = true;
  message.value = "";
  try {
    await api(`observations/${row.id}/exclude`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    excludeDialog.value?.close();
    await load();
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "排除观测记录失败";
  } finally {
    excluding.value = false;
  }
}

async function restore(row: Observation) {
  restoringId.value = row.id;
  message.value = "";
  try {
    await api(`observations/${row.id}/restore`, { method: "POST" });
    await load();
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "恢复观测记录失败";
  } finally {
    restoringId.value = null;
  }
}

async function confirmManualStart(row: Observation, reason: string) {
  manualStartId.value = row.id;
  message.value = "";
  try {
    await api(`observations/${row.id}/manual-start`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    manualStartDialog.value?.close();
    await load();
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "设置区间起点失败";
  } finally {
    manualStartId.value = null;
  }
}

async function clearManualStart(row: Observation) {
  manualStartId.value = row.id;
  message.value = "";
  try {
    await api(`observations/${row.id}/manual-start`, { method: "DELETE" });
    await load();
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "取消区间起点失败";
  } finally {
    manualStartId.value = null;
  }
}

async function rebuildCalculations() {
  if (
    !(await confirmDialog.value?.open({
      title: "重建当前区间计算？",
      message:
        "系统会保留全部原始采样、排除记录和管理员起点，从当前区间起点重新计算成本增量、百分比增量、折算率与参与者归属。",
      confirmLabel: "开始重建",
      tone: "warning",
    }))
  ) {
    return;
  }
  rebuilding.value = true;
  message.value = "";
  success.value = "";
  try {
    const result = await api<ObservationRebuildResult>("observations/rebuild", {
      method: "POST",
    });
    await load();
    success.value = `计算重建完成，共重算 ${result.rebuilt_observations} 条观测记录。`;
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "重建计算失败";
  } finally {
    rebuilding.value = false;
  }
}

function openFilter(kind: ObservationFilterKind) {
  filterDialog.value?.open(kind, filters);
}

function applyFilters(value: ObservationFilters) {
  Object.assign(filters, value);
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
          <li><h1>观测记录</h1></li>
        </ul>
      </div>
    </div>
    <button class="btn btn-primary btn-sm" :disabled="running" @click="run">
      <span v-if="running" class="loading loading-xs loading-spinner"></span>
      <AppIcon v-else name="play" class="size-4" />
      立即测算
    </button>
  </PageShellHeader>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>

  <div v-if="success" class="col-span-12 alert alert-success">
    <AppIcon name="check-circle" class="size-5" />
    <span>{{ success }}</span>
  </div>

  <ObservationStats
    :summary="summary"
    :schedule="schedule"
    :remaining-label="remainingLabel"
    :countdown-progress="countdownProgress"
  />

  <ObservationTable
    :rows="rows"
    :loading="loading"
    :pagination="pagination"
    :filters="filters"
    :restoring-id="restoringId"
    :manual-start-id="manualStartId"
    :rebuilding="rebuilding"
    :fast-correction-enabled="fastCorrectionEnabled"
    @filter="openFilter"
    @detail="detailDialog?.open($event)"
    @cost-detail="costDetailDialog?.open($event)"
    @fast-correction-detail="fastCorrectionDetailDialog?.open($event)"
    @exclude="excludeDialog?.open($event)"
    @restore="restore"
    @manual-start="manualStartDialog?.open($event)"
    @clear-manual-start="clearManualStart"
    @rebuild="rebuildCalculations"
    @page-change="changePage"
  />

  <ObservationFilterDialog ref="filterDialog" @apply="applyFilters" />
  <CostDeltaDetailDialog ref="costDetailDialog" />
  <FastCorrectionDetailDialog ref="fastCorrectionDetailDialog" />
  <ObservationDetailDialog ref="detailDialog" />
  <ExcludeObservationDialog
    ref="excludeDialog"
    :submitting="excluding"
    @confirm="confirmExclude"
  />
  <ManualStartDialog
    ref="manualStartDialog"
    :submitting="manualStartId !== null"
    @confirm="confirmManualStart"
  />
  <ConfirmDialog ref="confirmDialog" />
</template>
