<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api } from "@/services/api";
import type { MonitorSchedule, Observation } from "@/types";

const rows = ref<Observation[]>([]);
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
const validCount = computed(
  () => rows.value.filter((item) => item.valid_sample).length,
);
const passiveCount = computed(
  () => rows.value.filter((item) => item.query_mode === "passive").length,
);
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

function dateTime(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
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
  try {
    applySchedule(await api<MonitorSchedule>("monitor/run"));
  } catch {
    // 页面初次加载会展示错误；倒计时归零后的轻量刷新失败则等待下一轮重试。
  }
}

function tickCountdown() {
  clientNow.value = Date.now();
  if (
    schedule.value?.monitoring_enabled &&
    schedule.value.next_local_check_at &&
    remainingMs.value === 0 &&
    Date.now() - lastScheduleRefreshAt >= 5000
  ) {
    lastScheduleRefreshAt = Date.now();
    void refreshSchedule();
  }
}

async function load() {
  loading.value = true;
  try {
    const [observations, monitorSchedule] = await Promise.all([
      api<Observation[]>("observations?limit=100"),
      api<MonitorSchedule>("monitor/run"),
    ]);
    rows.value = observations;
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
      <div class="stat-figure">
        <AppIcon name="document-magnifying-glass" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">观测记录</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ rows.length }}
      </div>
      <div class="stat-desc">当前显示最近 100 条</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="check-circle" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">累计口径有效样本</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ validCount }}
      </div>
      <div class="stat-desc">本周期累计成本 ÷ 上游已用百分比</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="circle-stack" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">被动快照</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ passiveCount }}
      </div>
      <div class="stat-desc">未调用 OpenAI 官方额度接口</div>
    </div>
    <div v-if="schedule?.monitoring_enabled" class="stat">
      <div class="stat-figure">
        <AppIcon name="clock" class="size-7 opacity-40" />
      </div>
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
        {{ dateTime(schedule.next_local_check_at) }} · 全局探测全部启用参与者
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
      <div v-else class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>观测时间</th>
              <th>来源</th>
              <th>查询方式</th>
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
    </div>
  </section>

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
