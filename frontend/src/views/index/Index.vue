<script setup lang="ts">
import { onMounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import CalculationBasisHeader from "@/components/common/CalculationBasisHeader.vue";
import CalculationBasisTimeline from "@/components/common/CalculationBasisTimeline.vue";
import { ApiError, api } from "@/services/api";
import type { DashboardData } from "@/types";

const data = ref<DashboardData | null>(null);
const loading = ref(true);
const running = ref(false);
const message = ref("");
const rateBasisDialog = ref<HTMLDialogElement | null>(null);

function openRateBasis() {
  if (!data.value?.cycle?.rate_calculated) return;
  rateBasisDialog.value?.showModal();
}

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
      <div class="flex items-baseline gap-2">
        <div class="stat-value text-xl font-semibold tabular-nums">
          {{ currency(data.cycle?.effective_usd_per_percent) }}
        </div>
        <button
          v-if="data.cycle?.rate_calculated"
          type="button"
          class="cursor-pointer text-xs underline underline-offset-2 opacity-50"
          @click="openRateBasis"
        >
          查看依据
        </button>
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
                建议把 Sub2API 用户余额设置为
                <strong class="text-lg">{{
                  currency(participant.snapshot.recommended_balance_usd)
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
            该参与者在所选 OpenAI 上游账号内的本周期累计用量为
            {{ currency(participant.latest_selected_cost) }}。
          </p>
          <p class="mt-1 text-sm opacity-60">
            当前 Sub2API 用户余额为
            {{ currency(participant.latest_balance_usd) }}；
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
  <dialog ref="rateBasisDialog" class="modal">
    <div class="modal-box max-w-3xl">
      <form method="dialog">
        <button
          class="btn absolute top-3 right-3 btn-circle btn-ghost btn-sm"
          aria-label="关闭"
        >
          ✕
        </button>
      </form>
      <template v-if="data?.cycle?.rate_calculated">
        <CalculationBasisHeader
          title="保守美元 / 1% 计算依据"
          help="每个有效样本都从本周期 0 美元、0% 起算；系统按已用百分比加权后取保守分位。"
        />
        <CalculationBasisTimeline
          v-if="data.cycle.rate_samples[0]"
          :start-time="dateTime(data.cycle.starts_at)"
          start-value="$0.00 / 0.00%"
          end-label="最近有效样本终点"
          :end-time="dateTime(data.cycle.rate_samples[0].observed_at)"
          :end-value="`${currency(
            data.cycle.rate_samples[0].cost_usd,
          )} / ${percent(data.cycle.rate_samples[0].used_percent)}`"
        />
        <div
          v-if="data.cycle.rate_samples[0]"
          class="mt-3 rounded-box border border-base-300 p-4"
        >
          <div class="text-center text-sm font-semibold opacity-60">
            最近样本公式
          </div>
          <p
            class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
          >
            {{ currency(data.cycle.rate_samples[0].cost_usd) }} ÷
            {{ percent(data.cycle.rate_samples[0].used_percent) }} =
            {{ currency(data.cycle.rate_samples[0].usd_per_percent) }} / 1%
          </p>
          <p class="mt-2 text-sm opacity-70">
            最近
            {{ data.cycle.rate_sample_count }} 个有效样本按已用百分比加权，取
            {{ data.cycle.conservative_percentile }}% 保守分位，最终采用
            <strong>{{
              currency(data.cycle.effective_usd_per_percent)
            }}</strong>
            / 1%。
          </p>
        </div>
        <div class="mt-3 overflow-x-auto">
          <table class="table table-sm">
            <thead>
              <tr>
                <th>样本时间</th>
                <th>累计成本</th>
                <th>已用周限</th>
                <th>美元 / 1%</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="sample in data.cycle.rate_samples"
                :key="sample.observed_at"
              >
                <td>{{ dateTime(sample.observed_at) }}</td>
                <td>{{ currency(sample.cost_usd) }}</td>
                <td>{{ percent(sample.used_percent) }}</td>
                <td>{{ currency(sample.usd_per_percent) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
      <div class="modal-action">
        <form method="dialog">
          <button class="btn">关闭</button>
        </form>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop">
      <button>关闭</button>
    </form>
  </dialog>
</template>
