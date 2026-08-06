<script setup lang="ts">
import { onMounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api } from "@/services/api";
import type { DashboardData } from "@/types";

const data = ref<DashboardData | null>(null);
const loading = ref(true);
const running = ref(false);
const message = ref("");

function currency(value: number | null | undefined) {
  return value == null ? "—" : `$${value.toFixed(2)}`;
}

function percent(value: number | null | undefined) {
  return value == null ? "—" : `${value.toFixed(2)}%`;
}

function dateTime(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}

async function load() {
  loading.value = true;
  message.value = "";
  try {
    data.value = await api<DashboardData>("dashboard");
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "加载总览失败";
  } finally {
    loading.value = false;
  }
}

async function runCalibration() {
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

onMounted(load);
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">额度管理</RouterLink></li>
          <li><h1>额度总览</h1></li>
        </ul>
      </div>
    </div>
    <div class="flex flex-wrap gap-2">
      <button
        class="btn btn-primary btn-sm"
        :disabled="running"
        @click="runCalibration"
      >
        <span v-if="running" class="loading loading-xs loading-spinner"></span>
        <AppIcon v-else name="arrow-path" class="size-4" />
        {{ running ? "测算中" : "立即测算" }}
      </button>
      <RouterLink to="/settings" class="btn btn-sm">
        <AppIcon name="cog-6-tooth" class="size-4" />
        系统设置
      </RouterLink>
    </div>
  </PageShellHeader>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>
  <div
    v-if="data?.quota_query_mode === 'passive'"
    class="col-span-12 alert alert-info"
  >
    <AppIcon name="information-circle" class="size-5" />
    <span
      >当前为被动查询：只读取 Sub2API 已保存的账号快照，不会向 OpenAI
      官方额度接口发起请求。</span
    >
  </div>
  <div v-if="data && !data.configured" class="col-span-12 alert alert-warning">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span
      >尚未完成 Sub2API 连接配置。请先在系统设置中填写地址、Admin Token 和
      OpenAI 账号 ID。</span
    >
  </div>

  <section
    v-if="data"
    class="stats col-span-12 stats-vertical bg-base-200 shadow-xs xl:stats-horizontal"
  >
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="gauge" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">上游周限已用</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ percent(data.cycle?.upstream_used_percent) }}
      </div>
      <div class="stat-desc">按 OpenAI 七天窗口</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="banknotes" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">保守美元 / 1%</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ currency(data.cycle?.effective_usd_per_percent) }}
      </div>
      <div class="stat-desc">
        {{ data.cycle?.sample_note || "等待有效样本" }}
      </div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="clipboard-document-check" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">需要手动调整</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ data.needs_manual_update_count }}
      </div>
      <div class="stat-desc">本服务不会自动修改额度</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="calendar-days" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">上游重置时间</div>
      <div class="stat-value text-lg font-semibold tabular-nums">
        {{ dateTime(data.cycle?.resets_at) }}
      </div>
      <div class="stat-desc">
        快照：{{ dateTime(data.cycle?.snapshot_sampled_at) }}
      </div>
    </div>
  </section>

  <section v-if="loading" class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body items-center">
      <span class="loading loading-lg loading-spinner"></span>
    </div>
  </section>

  <section v-if="data" class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <h2 class="card-title">
        <AppIcon name="sparkles" class="size-5" />
        当前额度建议
      </h2>
      <div v-if="data.participants.length" class="grid gap-3">
        <article
          v-for="participant in data.participants"
          :key="participant.id"
          class="rounded-box border border-base-300 bg-base-100 p-4"
        >
          <div class="flex flex-wrap items-start justify-between gap-3">
            <p class="leading-7">
              对于参与者
              <strong>{{ participant.name }}</strong>
              （Sub2API 账号
              <span class="font-mono">{{ participant.sub2api_user_id }}</span
              >），
              <template v-if="participant.snapshot">
                建议把 OpenAI 周限额设置为
                <strong class="text-lg">{{
                  currency(participant.snapshot.recommended_weekly_limit_usd)
                }}</strong
                >。
              </template>
              <template v-else> 尚无额度建议，请先完成一次有效测算。 </template>
            </p>
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
                    ? "建议手动调整"
                    : "当前无需调整"
              }}
            </span>
          </div>
          <p class="mt-2 text-sm opacity-60">
            当前 Sub2API 周用量为
            {{ currency(participant.latest_weekly_usage_usd) }}，现有限额为
            {{ currency(participant.latest_weekly_limit_usd) }}；
            {{ participant.snapshot?.reason || "等待首次测算后生成依据" }}。
          </p>
          <p v-if="participant.snapshot" class="mt-1 text-sm opacity-60">
            已归属上游周限
            {{ percent(participant.snapshot.charged_cycle_percent) }}，剩余权益
            {{ percent(participant.snapshot.remaining_share_percent) }}。
          </p>
        </article>
      </div>
      <div v-else class="py-6 text-center opacity-60">
        尚未添加参与者，无法生成额度建议。
      </div>
    </div>
  </section>

  <section
    v-if="data"
    class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6"
  >
    <div class="card-body">
      <h2 class="card-title">
        <AppIcon name="signal" class="size-5" />采集状态
      </h2>
      <div class="overflow-x-auto">
        <table class="table table-sm">
          <tbody>
            <tr>
              <th>本地用量探测</th>
              <td>{{ dateTime(data.last_local_check_at) }}</td>
            </tr>
            <tr>
              <th>额度快照读取</th>
              <td>{{ dateTime(data.last_upstream_check_at) }}</td>
            </tr>
            <tr>
              <th>最近成功</th>
              <td>{{ dateTime(data.last_success_at) }}</td>
            </tr>
            <tr>
              <th>运行状态</th>
              <td>{{ data.monitoring_enabled ? "已启用" : "已停用" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>

  <section
    v-if="data"
    class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6"
  >
    <div class="card-body">
      <h2 class="card-title">
        <AppIcon name="information-circle" class="size-5" />账本说明
      </h2>
      <p class="text-sm leading-6 opacity-70">
        每次有效观测把上游百分比增量按参与者同期美元用量占账号总用量的比例归属。美元限额只是一条手动调整建议；
        百分比权益账本才是最终依据，因此参与者可以在任意时间集中使用自己的全部权益。
      </p>
      <div class="divider my-1"></div>
      <p class="text-sm">
        未归属的已用周限：<strong>{{
          percent(data.cycle?.unattributed_used_percent)
        }}</strong>
      </p>
      <p v-if="data.last_error" class="text-sm text-error">
        {{ data.last_error }}
      </p>
    </div>
  </section>
</template>
