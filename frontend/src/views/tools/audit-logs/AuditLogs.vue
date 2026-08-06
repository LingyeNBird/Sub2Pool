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
const summary = reactive({ total: 0, valid_count: 0, passive_count: 0 });
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
              <tr v-for="row in rows" :key="row.id">
                <td>
                  <div>{{ dateTime(row.observed_at) }}</div>
                  <div class="text-xs opacity-60">
                    快照 {{ dateTime(row.snapshot_sampled_at) }}
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
                <td>{{ currency(row.delta_cost) }}</td>
                <td>{{ percent(row.delta_percent) }}</td>
                <td>{{ currency(row.sample_usd_per_percent) }}</td>
                <td class="font-semibold">
                  {{ currency(row.effective_usd_per_percent) }}
                </td>
                <td>
                  <button class="btn btn-ghost btn-xs" @click="show(row)">
                    详情
                  </button>
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

  <dialog ref="dialog" class="modal">
    <div class="modal-box max-w-4xl">
      <h2 class="text-lg font-bold">观测详情</h2>
      <p class="mt-1 text-sm opacity-60">{{ selected?.sample_note }}</p>
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
</template>
