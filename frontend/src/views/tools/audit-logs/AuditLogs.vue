<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import PaginationControls from "@/components/common/PaginationControls.vue";
import { useDateTime, useZonedDateTimeIso } from "@/composables/useDateTime";
import { ApiError, api } from "@/services/api";
import type {
  MonitorSchedule,
  Observation,
  ObservationListData,
  PaginationMeta,
} from "@/types";

type FilterKind = "time" | "source" | "query";

const dateTime = useDateTime();
const toIso = useZonedDateTimeIso();
const rows = ref<Observation[]>([]);
const summary = reactive({
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
const filters = reactive({
  from: "",
  to: "",
  source: "",
  query_mode: "",
});
const draft = reactive({ from: "", to: "", source: "", query_mode: "" });
const filterKind = ref<FilterKind>("time");
const filterDialog = ref<HTMLDialogElement | null>(null);
const loading = ref(true);
const running = ref(false);
const message = ref("");
const selected = ref<Observation | null>(null);
const dialog = ref<HTMLDialogElement | null>(null);
const costDetail = ref<Observation | null>(null);
const costDialog = ref<HTMLDialogElement | null>(null);
const exclusionTarget = ref<Observation | null>(null);
const exclusionReason = ref("");
const excludeDialog = ref<HTMLDialogElement | null>(null);
const excluding = ref(false);
const restoringId = ref<number | null>(null);
const manualStartTarget = ref<Observation | null>(null);
const manualStartReason = ref("");
const manualStartDialog = ref<HTMLDialogElement | null>(null);
const manualStartId = ref<number | null>(null);
const schedule = ref<MonitorSchedule | null>(null);
const clientNow = ref(Date.now());
const serverOffsetMs = ref(0);
let clockTimer: number | undefined;
let lastScheduleRefreshAt = 0;
let expiredScheduleAt: string | null = null;
let refreshingSchedule = false;

const remainingMs = computed(() => {
  if (
    !schedule.value?.monitoring_enabled ||
    !schedule.value.next_local_check_at
  )
    return 0;
  const nextAt = new Date(schedule.value.next_local_check_at).getTime();
  return Math.max(0, nextAt - (clientNow.value + serverOffsetMs.value));
});
const countdownProgress = computed(() => {
  const intervalMs = (schedule.value?.interval_seconds ?? 0) * 1000;
  if (!intervalMs) return 0;
  return Math.min(100, (remainingMs.value / intervalMs) * 100);
});
const remainingLabel = computed(() => {
  if (!schedule.value?.next_local_check_at) return "等待轮询器登记";
  const seconds = Math.max(0, Math.ceil(remainingMs.value / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  if (hours) return `${hours} 小时 ${minutes} 分 ${remainder} 秒`;
  return `${minutes} 分 ${remainder} 秒`;
});
const participantDeltaTotal = computed(() =>
  Number(
    (
      costDetail.value?.participants.reduce(
        (total, item) => total + (item.delta_cost ?? 0),
        0,
      ) ?? 0
    ).toFixed(6),
  ),
);
const unmatchedCostDelta = computed(() => {
  if (costDetail.value?.delta_cost == null) return null;
  const difference = costDetail.value.delta_cost - participantDeltaTotal.value;
  return Math.abs(difference) < 0.000001 ? 0 : Number(difference.toFixed(6));
});

function currency(value: number | null) {
  return value == null ? "—" : `$${value.toFixed(2)}`;
}

function percent(value: number | null) {
  return value == null ? "—" : `${value.toFixed(2)}%`;
}

function sourceLabel(value: string) {
  return (
    {
      manual: "手动",
      scheduled: "定时",
      exhausted: "额度耗尽",
      reset: "临近重置",
    }[value] ?? value
  );
}

function applySchedule(value: MonitorSchedule) {
  schedule.value = value;
  serverOffsetMs.value = new Date(value.server_time).getTime() - Date.now();
  lastScheduleRefreshAt = Date.now();
}

async function refreshSchedule() {
  if (refreshingSchedule) return;
  refreshingSchedule = true;
  try {
    const awaitedSchedule = expiredScheduleAt;
    const wasRunning = schedule.value?.run_in_progress ?? false;
    const value = await api<MonitorSchedule>("monitor/run");
    const scheduleAdvanced = Boolean(
      awaitedSchedule &&
      value.next_local_check_at &&
      new Date(value.next_local_check_at).getTime() >
        new Date(awaitedSchedule).getTime(),
    );
    applySchedule(value);
    if (!value.monitoring_enabled) expiredScheduleAt = null;
    if ((scheduleAdvanced || wasRunning) && !value.run_in_progress) {
      expiredScheduleAt = null;
      await load();
    }
  } catch {
    // 后台任务尚未登记下一时隙时继续轮询，不覆盖表格自身的加载错误。
  } finally {
    refreshingSchedule = false;
  }
}

function tickCountdown() {
  clientNow.value = Date.now();
  if (
    remainingMs.value === 0 &&
    schedule.value?.monitoring_enabled &&
    schedule.value.next_local_check_at &&
    !expiredScheduleAt
  ) {
    expiredScheduleAt = schedule.value.next_local_check_at;
  }
  if (
    schedule.value?.monitoring_enabled &&
    (expiredScheduleAt || schedule.value.run_in_progress) &&
    Date.now() - lastScheduleRefreshAt >= 5000
  ) {
    lastScheduleRefreshAt = Date.now();
    void refreshSchedule();
  }
}

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

function show(row: Observation) {
  selected.value = row;
  dialog.value?.showModal();
}

function showCostDetail(row: Observation) {
  if (row.delta_cost == null) return;
  costDetail.value = row;
  costDialog.value?.showModal();
}

function promptExclude(row: Observation) {
  exclusionTarget.value = row;
  exclusionReason.value = "";
  excludeDialog.value?.showModal();
}

async function confirmExclude() {
  if (!exclusionTarget.value) return;
  excluding.value = true;
  message.value = "";
  try {
    await api(`observations/${exclusionTarget.value.id}/exclude`, {
      method: "POST",
      body: JSON.stringify({ reason: exclusionReason.value }),
    });
    excludeDialog.value?.close();
    exclusionTarget.value = null;
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

function promptManualStart(row: Observation) {
  manualStartTarget.value = row;
  manualStartReason.value = "";
  manualStartDialog.value?.showModal();
}

async function confirmManualStart() {
  if (!manualStartTarget.value) return;
  manualStartId.value = manualStartTarget.value.id;
  message.value = "";
  try {
    await api(`observations/${manualStartTarget.value.id}/manual-start`, {
      method: "POST",
      body: JSON.stringify({ reason: manualStartReason.value }),
    });
    manualStartDialog.value?.close();
    manualStartTarget.value = null;
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

function openFilter(kind: FilterKind) {
  filterKind.value = kind;
  Object.assign(draft, filters);
  filterDialog.value?.showModal();
}

function applyFilter() {
  if (filterKind.value === "time") {
    filters.from = draft.from;
    filters.to = draft.to;
  } else if (filterKind.value === "source") {
    filters.source = draft.source;
  } else {
    filters.query_mode = draft.query_mode;
  }
  pagination.value.page = 1;
  filterDialog.value?.close();
  void load();
}

function clearFilter() {
  if (filterKind.value === "time") {
    filters.from = "";
    filters.to = "";
  } else if (filterKind.value === "source") {
    filters.source = "";
  } else {
    filters.query_mode = "";
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

onMounted(() => {
  void load();
  clockTimer = window.setInterval(tickCountdown, 1000);
});
onUnmounted(() => window.clearInterval(clockTimer));
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

  <section
    class="stats col-span-12 stats-vertical bg-base-200 shadow-xs xl:stats-horizontal"
  >
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">观测记录</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ summary.total }}
          </div>
          <div class="stat-desc">符合当前筛选条件</div>
        </div>
        <AppIcon
          name="document-magnifying-glass"
          class="size-7 shrink-0 opacity-40"
        />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">累计口径有效样本</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ summary.valid_count }}
          </div>
          <div class="stat-desc">本周期累计成本 ÷ 上游已用百分比</div>
        </div>
        <AppIcon name="check-circle" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">被动快照</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ summary.passive_count }}
          </div>
          <div class="stat-desc">未调用 OpenAI 官方额度接口</div>
        </div>
        <AppIcon name="circle-stack" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div v-if="schedule?.monitoring_enabled" class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0 grow">
          <div class="stat-title">下次自动采样（本地探测）</div>
          <div class="stat-value text-lg font-semibold tabular-nums">
            {{ remainingLabel }}
          </div>
          <progress
            class="progress mt-2 w-full progress-primary"
            :value="countdownProgress"
            max="100"
          ></progress>
          <div class="stat-desc">
            {{ dateTime(schedule.next_local_check_at) }} ·
            全局探测全部启用参与者
          </div>
        </div>
        <AppIcon name="clock" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
  </section>

  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <h2 class="card-title">
        <AppIcon name="document-magnifying-glass" class="size-5" />校准历史
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
                    class="btn h-auto min-h-0 btn-ghost p-0 text-sm"
                    @click="openFilter('time')"
                  >
                    观测时间
                    <span v-if="filters.from || filters.to" class="text-primary"
                      >●</span
                    >
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 text-sm"
                    @click="openFilter('source')"
                  >
                    来源
                    <span v-if="filters.source" class="text-primary">●</span>
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 text-sm"
                    @click="openFilter('query')"
                  >
                    查询方式
                    <span v-if="filters.query_mode" class="text-primary"
                      >●</span
                    >
                  </button>
                </th>
                <th>上游已用</th>
                <th>成本增量</th>
                <th>百分比增量</th>
                <th>累计样本美元 / 1%</th>
                <th>采用值</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in rows"
                :key="row.id"
                :class="{ 'opacity-55': row.excluded }"
              >
                <td>
                  <div>{{ dateTime(row.observed_at) }}</div>
                  <div class="text-xs opacity-60">
                    快照 {{ dateTime(row.snapshot_sampled_at) }}
                  </div>
                  <div
                    v-if="row.excluded || row.is_manual_start"
                    class="mt-1 flex flex-wrap gap-1"
                  >
                    <template v-if="row.excluded">
                      <span class="badge badge-sm badge-warning">已排除</span>
                      <span class="badge badge-ghost badge-sm">
                        {{
                          row.exclusion_source === "automatic"
                            ? "自动判定"
                            : "管理员"
                        }}
                      </span>
                    </template>
                    <span
                      v-if="row.is_manual_start"
                      class="badge badge-sm badge-primary"
                    >
                      管理员起点
                    </span>
                  </div>
                </td>
                <td>
                  <span class="badge badge-ghost badge-sm">{{
                    sourceLabel(row.source)
                  }}</span>
                </td>
                <td>
                  <span
                    class="badge badge-sm"
                    :class="
                      row.query_mode === 'passive'
                        ? 'badge-info'
                        : 'badge-warning'
                    "
                  >
                    {{ row.query_mode === "passive" ? "被动" : "上游直查" }}
                  </span>
                </td>
                <td>{{ percent(row.upstream_used_percent) }}</td>
                <td>
                  <button
                    v-if="row.delta_cost !== null"
                    type="button"
                    class="link cursor-pointer font-medium tabular-nums link-hover"
                    @click="showCostDetail(row)"
                  >
                    {{ currency(row.delta_cost) }}
                  </button>
                  <span v-else>—</span>
                </td>
                <td>{{ percent(row.delta_percent) }}</td>
                <td>{{ currency(row.sample_usd_per_percent) }}</td>
                <td class="font-semibold">
                  {{ currency(row.effective_usd_per_percent) }}
                </td>
                <td>
                  <div class="flex items-center gap-1">
                    <button class="btn btn-ghost btn-xs" @click="show(row)">
                      详情
                    </button>
                    <template v-if="row.excluded">
                      <button
                        v-if="row.exclusion_source === 'automatic'"
                        class="btn btn-ghost text-primary btn-xs"
                        :disabled="manualStartId === row.id"
                        @click="promptManualStart(row)"
                      >
                        设为起点
                      </button>
                      <button
                        v-else
                        class="btn btn-ghost text-success btn-xs"
                        :disabled="restoringId === row.id"
                        @click="restore(row)"
                      >
                        <span
                          v-if="restoringId === row.id"
                          class="loading loading-xs loading-spinner"
                        ></span>
                        恢复
                      </button>
                    </template>
                    <template v-else>
                      <button
                        v-if="row.is_manual_start"
                        class="btn btn-ghost text-primary btn-xs"
                        :disabled="manualStartId === row.id"
                        @click="clearManualStart(row)"
                      >
                        <span
                          v-if="manualStartId === row.id"
                          class="loading loading-xs loading-spinner"
                        ></span>
                        取消起点
                      </button>
                      <button
                        v-else
                        class="btn btn-ghost text-primary btn-xs"
                        @click="promptManualStart(row)"
                      >
                        设为起点
                      </button>
                      <button
                        class="btn btn-ghost text-warning btn-xs"
                        @click="promptExclude(row)"
                      >
                        排除
                      </button>
                    </template>
                  </div>
                </td>
              </tr>
              <tr v-if="rows.length === 0">
                <td colspan="9" class="py-8 text-center opacity-60">
                  尚无观测记录
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
            ? "筛选观测时间"
            : filterKind === "source"
              ? "筛选观测来源"
              : "筛选查询方式"
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
      <fieldset v-else-if="filterKind === 'source'" class="mt-4 fieldset">
        <label class="label">来源</label>
        <select v-model="draft.source" class="select w-full">
          <option value="">全部来源</option>
          <option value="manual">手动</option>
          <option value="scheduled">定时</option>
          <option value="exhausted">额度耗尽</option>
          <option value="reset">临近重置</option>
        </select>
      </fieldset>
      <fieldset v-else class="mt-4 fieldset">
        <label class="label">查询方式</label>
        <select v-model="draft.query_mode" class="select w-full">
          <option value="">全部方式</option>
          <option value="passive">被动快照</option>
          <option value="direct">上游直查</option>
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

  <dialog ref="costDialog" class="modal">
    <div class="modal-box max-w-3xl">
      <h2 class="text-lg font-bold">成本增量明细</h2>
      <p v-if="costDetail" class="mt-1 text-sm opacity-60">
        {{ dateTime(costDetail.observed_at) }} 相对上一条有效观测
      </p>
      <div class="mt-4 overflow-x-auto">
        <table class="table table-sm">
          <thead>
            <tr>
              <th>参与者</th>
              <th>上一点累计成本</th>
              <th>当前累计成本</th>
              <th>成本增量</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in costDetail?.participants"
              :key="item.participant_id"
            >
              <td>{{ item.participant_name }}</td>
              <td class="tabular-nums">
                {{
                  item.delta_cost === null
                    ? "无上一观测快照"
                    : currency(item.selected_cost - item.delta_cost)
                }}
              </td>
              <td class="tabular-nums">{{ currency(item.selected_cost) }}</td>
              <td class="font-medium tabular-nums">
                {{ currency(item.delta_cost) }}
              </td>
            </tr>
            <tr v-if="costDetail?.participants.length === 0">
              <td colspan="4" class="py-6 text-center opacity-60">
                此观测没有参与者快照
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <th colspan="3">已知参与者成本增量合计</th>
              <th class="tabular-nums">
                {{ currency(participantDeltaTotal) }}
              </th>
            </tr>
            <tr>
              <th colspan="3">账号总成本增量</th>
              <th class="tabular-nums">
                {{ currency(costDetail?.delta_cost ?? null) }}
              </th>
            </tr>
            <tr v-if="unmatchedCostDelta !== null && unmatchedCostDelta !== 0">
              <th colspan="3">未映射或无法逐用户还原</th>
              <th class="tabular-nums">
                {{ currency(unmatchedCostDelta) }}
              </th>
            </tr>
          </tfoot>
        </table>
      </div>
      <div
        v-if="costDetail?.participants.some((item) => item.delta_cost === null)"
        class="mt-4 alert text-sm alert-info"
      >
        <AppIcon name="information-circle" class="size-5" />
        <span>
          首次出现在观测中的参与者没有上一点快照；系统会保留其当前周期累计成本，但不会伪造该采样区间的个人增量。
        </span>
      </div>
      <div class="modal-action">
        <button class="btn" @click="costDialog?.close()">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>

  <dialog ref="dialog" class="modal">
    <div class="modal-box max-w-4xl">
      <h2 class="text-lg font-bold">观测详情</h2>
      <p class="mt-1 text-sm opacity-60">{{ selected?.sample_note }}</p>
      <div v-if="selected?.excluded" class="mt-4 alert alert-warning">
        <AppIcon name="exclamation-triangle" class="size-5" />
        <span> 此记录已排除，不参与计算。{{ selected.exclusion_reason }} </span>
      </div>
      <div v-if="selected?.is_manual_start" class="mt-4 alert alert-info">
        <AppIcon name="flag" class="size-5" />
        <span>
          此记录是管理员指定的区间起点。{{
            selected.manual_start_reason || "未填写起点说明"
          }}
        </span>
      </div>
      <div class="mt-4 overflow-x-auto">
        <table class="table table-sm">
          <thead>
            <tr>
              <th>参与者</th>
              <th>成本增量</th>
              <th>本次归属</th>
              <th>累计归属</th>
              <th>剩余权益</th>
              <th>建议用户余额</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in selected?.participants"
              :key="item.participant_id"
            >
              <td>{{ item.participant_name }}</td>
              <td>{{ currency(item.delta_cost) }}</td>
              <td>{{ percent(item.charged_delta_percent) }}</td>
              <td>{{ percent(item.charged_cycle_percent) }}</td>
              <td>{{ percent(item.remaining_share_percent) }}</td>
              <td>{{ currency(item.recommended_balance_usd) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="modal-action">
        <button class="btn" @click="dialog?.close()">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>

  <dialog ref="excludeDialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">排除校准记录</h2>
      <p class="mt-3 text-sm opacity-70">
        原始记录会永久保留作审计。系统将忽略该点，并只从它所在的最早受影响区间起点向后重算；更早的稳定区间不会重复重放。
      </p>
      <div v-if="exclusionTarget" class="mt-4 rounded-box bg-base-300 p-4">
        <div class="font-medium">
          {{ dateTime(exclusionTarget.observed_at) }}
        </div>
        <div class="mt-1 text-sm opacity-70">
          上游已用 {{ percent(exclusionTarget.upstream_used_percent) }} ·
          累计成本 {{ currency(exclusionTarget.selected_total_cost) }}
        </div>
      </div>
      <fieldset class="mt-4 fieldset">
        <label class="label">排除原因（可选）</label>
        <input
          v-model="exclusionReason"
          class="input w-full"
          maxlength="255"
          placeholder="例如：上游返回了一次异常百分比"
        />
      </fieldset>
      <div class="modal-action">
        <button
          type="button"
          class="btn"
          :disabled="excluding"
          @click="excludeDialog?.close()"
        >
          取消
        </button>
        <button
          type="button"
          class="btn btn-warning"
          :disabled="excluding"
          @click="confirmExclude"
        >
          <span
            v-if="excluding"
            class="loading loading-xs loading-spinner"
          ></span>
          确认排除
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
  <dialog ref="manualStartDialog" class="modal">
    <div class="modal-box">
      <h2 class="text-lg font-bold">设置管理员区间起点</h2>
      <p class="mt-3 text-sm leading-6 opacity-70">
        管理员起点优先于上游重置时间。系统会把所选观测的累计成本和上游百分比作为零基线，只重算该点及其后续记录；请仅在确认这里发生了官方赠送刷新或其他真实边界时使用。
      </p>
      <div v-if="manualStartTarget" class="mt-4 rounded-box bg-base-300 p-4">
        <div class="font-medium">
          {{ dateTime(manualStartTarget.observed_at) }}
        </div>
        <div class="mt-1 text-sm opacity-70">
          上游已用 {{ percent(manualStartTarget.upstream_used_percent) }} ·
          原始累计成本 {{ currency(manualStartTarget.raw_selected_total_cost) }}
        </div>
      </div>
      <fieldset class="mt-4 fieldset">
        <label class="label">起点说明（可选）</label>
        <input
          v-model="manualStartReason"
          class="input w-full"
          maxlength="255"
          placeholder="例如：管理员确认此处为官方赠送刷新"
        />
      </fieldset>
      <div class="modal-action">
        <button
          type="button"
          class="btn"
          :disabled="manualStartId !== null"
          @click="manualStartDialog?.close()"
        >
          取消
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="manualStartId !== null"
          @click="confirmManualStart"
        >
          <span
            v-if="manualStartId !== null"
            class="loading loading-xs loading-spinner"
          ></span>
          确认设为起点
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
