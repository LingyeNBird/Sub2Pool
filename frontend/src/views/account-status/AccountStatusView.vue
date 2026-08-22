<script setup lang="ts">
import { onMounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { useDateTime } from "@/composables/useDateTime";
import { ApiError, api } from "@/services/api";
import type { AccountStatusAccount, AccountStatusData } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

const data = ref<AccountStatusData | null>(null);
const loading = ref(true);
const message = ref("");
const dateTime = useDateTime();

async function load() {
  loading.value = true;
  message.value = "";
  try {
    data.value = await api<AccountStatusData>("account-status");
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "加载账号状态失败";
  } finally {
    loading.value = false;
  }
}

function accountName(account: AccountStatusAccount): string {
  return account.runtime?.name || account.name;
}

function statusLabel(account: AccountStatusAccount): string {
  if (account.usage?.is_banned) return "已封禁";
  if (account.usage?.needs_reauth) return "需重新授权";
  if (account.usage?.needs_verify) return "需人工验证";
  if (
    account.runtime?.status === "active" &&
    account.runtime.schedulable === false
  ) {
    return "不可调度";
  }
  const labels: Record<string, string> = {
    active: "正常",
    disabled: "已禁用",
    error: "异常",
    rate_limited: "受限",
  };
  const status = account.runtime?.status;
  return status ? (labels[status] ?? status) : "状态未知";
}

function statusClass(account: AccountStatusAccount): string {
  if (
    account.runtime?.status === "error" ||
    account.usage?.is_banned ||
    account.usage?.needs_reauth
  ) {
    return "badge-error";
  }
  if (account.usage?.needs_verify) {
    return "badge-warning";
  }
  if (account.runtime?.status === "active" && account.runtime.schedulable) {
    return "badge-success";
  }
  return "badge-warning";
}

function progressClass(value: number | null | undefined): string {
  if (value == null) return "progress-neutral";
  if (value >= 95) return "progress-error";
  if (value >= 80) return "progress-warning";
  return "progress-success";
}

function formatCount(
  value: number | null | undefined,
  maximumFractionDigits = 0,
): string {
  return value == null
    ? "—"
    : value.toLocaleString("zh-CN", { maximumFractionDigits });
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return "";
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟`;
  return `${minutes} 分钟`;
}

function hasRuntimeDetails(account: AccountStatusAccount): boolean {
  const runtime = account.runtime;
  const usage = account.usage;
  return Boolean(
    usage?.five_hour ||
    usage?.needs_reauth ||
    usage?.needs_verify ||
    usage?.is_banned ||
    (runtime &&
      (runtime.last_used_at ||
        runtime.rate_limited_at ||
        runtime.rate_limit_reset_at ||
        runtime.overload_until ||
        runtime.temp_unschedulable_until ||
        runtime.error_message ||
        runtime.temp_unschedulable_reason ||
        runtime.current_concurrency != null)),
  );
}

onMounted(load);
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">额度管理</RouterLink></li>
          <li><h1>账号状态</h1></li>
        </ul>
      </div>
      <p class="mt-1 max-w-3xl text-sm opacity-60">
        直接读取 Sub2API
        保存的账号状态、额度窗口与请求统计；不同版本未提供的字段不会显示。
      </p>
    </div>
    <div class="flex items-center gap-3">
      <span v-if="data" class="hidden text-xs opacity-50 sm:inline">
        查询于 {{ dateTime(data.sampled_at) }}
      </span>
      <button class="btn btn-sm" :disabled="loading" @click="load">
        <span v-if="loading" class="loading loading-xs loading-spinner"></span>
        <AppIcon v-else name="arrow-path" class="size-4" />
        刷新
      </button>
    </div>
  </PageShellHeader>

  <div class="col-span-12 alert alert-info">
    <AppIcon name="information-circle" class="size-5" />
    <span>
      额度窗口使用 Sub2API 被动快照，不会因打开或刷新本页而请求 OpenAI
      官方额度接口；近 {{ data?.stats_days ?? 30 }} 天统计来自 Sub2API
      本地请求日志， FAST 修正来自本地已持久化事实。
    </span>
  </div>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>
  <div v-if="data?.connection_error" class="col-span-12 alert alert-warning">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ data.connection_error }}</span>
  </div>

  <section
    v-if="loading && !data"
    class="card col-span-12 bg-base-200 shadow-xs"
  >
    <div class="card-body items-center py-16">
      <span class="loading loading-lg loading-spinner"></span>
      <span class="text-sm opacity-60">正在读取 Sub2API 账号状态</span>
    </div>
  </section>

  <section
    v-else-if="data && data.accounts.length === 0"
    class="card col-span-12 border border-base-300 bg-base-100"
  >
    <div class="card-body items-center py-16 text-center">
      <AppIcon name="server" class="size-10 opacity-30" />
      <h2 class="card-title">尚未添加监控账号</h2>
      <p class="text-sm opacity-60">
        请先在系统设置中连接 Sub2API，并添加要查看的 OpenAI 上游账号。
      </p>
      <RouterLink to="/settings" class="btn mt-2 btn-primary btn-sm">
        前往系统设置
      </RouterLink>
    </div>
  </section>

  <article
    v-for="account in data?.accounts ?? []"
    :key="account.id"
    class="card col-span-12 overflow-hidden border border-base-300 bg-base-100 shadow-xs"
  >
    <div class="card-body gap-6 p-5 sm:p-6">
      <header
        class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"
      >
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <h2 class="card-title truncate">{{ accountName(account) }}</h2>
            <span class="badge badge-sm" :class="statusClass(account)">
              {{ statusLabel(account) }}
            </span>
            <span
              v-if="account.runtime?.schedulable != null"
              class="badge badge-outline badge-sm"
            >
              {{ account.runtime.schedulable ? "可调度" : "不可调度" }}
            </span>
            <span v-if="!account.enabled" class="badge badge-ghost badge-sm">
              本地监控已停用
            </span>
          </div>
          <p class="mt-1 text-xs opacity-50">
            Sub2API #{{ account.external_account_id }}
            <template v-if="account.runtime?.account_type">
              · {{ account.runtime.account_type }}
            </template>
            <template
              v-if="
                account.runtime?.name && account.runtime.name !== account.name
              "
            >
              · 本地名称 {{ account.name }}
            </template>
          </p>
        </div>
        <div v-if="account.usage?.updated_at" class="text-xs opacity-50">
          额度快照 {{ dateTime(account.usage.updated_at) }}
        </div>
      </header>

      <section
        v-if="account.usage?.seven_day"
        class="grid gap-4 lg:grid-cols-12"
      >
        <div class="rounded-box bg-base-200 p-5 lg:col-span-4">
          <div class="flex items-end justify-between gap-4">
            <div>
              <div class="text-xs font-medium tracking-wide opacity-55">
                7 天窗口已用
              </div>
              <div class="mt-1 text-3xl font-semibold tabular-nums">
                {{ formatPercent(account.usage.seven_day.used_percent) }}
              </div>
            </div>
            <AppIcon name="gauge" class="size-8 opacity-25" />
          </div>
          <progress
            v-if="account.usage.seven_day.used_percent != null"
            class="progress mt-4 w-full"
            :class="progressClass(account.usage.seven_day.used_percent)"
            :value="Math.min(100, account.usage.seven_day.used_percent)"
            max="100"
          ></progress>
          <div
            v-if="account.usage.seven_day.reset_at"
            class="mt-3 flex flex-wrap justify-between gap-x-3 gap-y-1 text-xs"
          >
            <span class="opacity-55">重置时间</span>
            <span class="text-right font-medium">
              {{ dateTime(account.usage.seven_day.reset_at) }}
              <span
                v-if="formatDuration(account.usage.seven_day.remaining_seconds)"
                class="block font-normal opacity-55"
              >
                剩余
                {{ formatDuration(account.usage.seven_day.remaining_seconds) }}
              </span>
            </span>
          </div>
        </div>

        <div
          class="grid gap-3 sm:grid-cols-3 lg:col-span-8"
          aria-label="七天窗口用量"
        >
          <div
            v-if="account.usage.seven_day.account_cost_usd != null"
            class="rounded-box border border-base-300 p-4"
          >
            <div class="text-xs opacity-55">周期已用金额</div>
            <div class="mt-2 text-2xl font-semibold tabular-nums">
              {{ formatCurrency(account.usage.seven_day.account_cost_usd) }}
            </div>
            <div class="mt-1 space-y-0.5 text-xs opacity-45">
              <div>账号成本口径</div>
              <div v-if="account.usage.seven_day.standard_cost_usd != null">
                标准成本
                {{ formatCurrency(account.usage.seven_day.standard_cost_usd) }}
              </div>
              <div v-if="account.usage.seven_day.user_cost_usd != null">
                用户扣费
                {{ formatCurrency(account.usage.seven_day.user_cost_usd) }}
              </div>
            </div>
          </div>
          <div
            v-if="account.usage.seven_day.request_count != null"
            class="rounded-box border border-base-300 p-4"
          >
            <div class="text-xs opacity-55">周期请求数</div>
            <div class="mt-2 text-2xl font-semibold tabular-nums">
              {{ formatCount(account.usage.seven_day.request_count) }}
            </div>
          </div>
          <div
            v-if="account.usage.seven_day.token_count != null"
            class="rounded-box border border-base-300 p-4"
          >
            <div class="text-xs opacity-55">周期 Token</div>
            <div class="mt-2 text-2xl font-semibold tabular-nums">
              {{ formatCount(account.usage.seven_day.token_count) }}
            </div>
          </div>
        </div>
      </section>

      <section v-if="account.stats" class="border-t border-base-300 pt-5">
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 class="font-semibold">
            近 {{ account.stats.days ?? data?.stats_days ?? 30 }} 天累计
          </h3>
          <span
            v-if="account.stats.actual_days_used != null"
            class="text-xs opacity-45"
          >
            实际有请求 {{ account.stats.actual_days_used }} 天
          </span>
        </div>
        <div class="grid gap-x-6 gap-y-4 sm:grid-cols-2 xl:grid-cols-4">
          <div v-if="account.stats.account_cost_usd != null">
            <div class="text-xs opacity-55">账号成本</div>
            <div class="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span class="text-xl font-semibold tabular-nums">
                {{ formatCurrency(account.stats.account_cost_usd) }}
              </span>
              <span
                v-if="account.stats.fast_correction_usd != null"
                class="text-xs font-normal opacity-60"
              >
                （FAST 修正 +{{
                  formatCurrency(account.stats.fast_correction_usd)
                }}）
              </span>
            </div>
            <div
              v-if="account.stats.account_cost_with_fast_correction_usd != null"
              class="mt-1 text-sm font-medium"
            >
              含 FAST 修正
              {{
                formatCurrency(
                  account.stats.account_cost_with_fast_correction_usd,
                )
              }}
            </div>
          </div>

          <div v-if="account.stats.request_count != null">
            <div class="text-xs opacity-55">请求数</div>
            <div class="mt-1 text-xl font-semibold tabular-nums">
              {{ formatCount(account.stats.request_count) }}
            </div>
            <div
              v-if="account.stats.avg_daily_request_count != null"
              class="mt-1 text-xs opacity-45"
            >
              日均 {{ formatCount(account.stats.avg_daily_request_count, 1) }}
            </div>
          </div>
          <div v-if="account.stats.token_count != null">
            <div class="text-xs opacity-55">Token</div>
            <div class="mt-1 text-xl font-semibold tabular-nums">
              {{ formatCount(account.stats.token_count) }}
            </div>
            <div
              v-if="account.stats.avg_daily_token_count != null"
              class="mt-1 text-xs opacity-45"
            >
              日均 {{ formatCount(account.stats.avg_daily_token_count) }}
            </div>
          </div>
          <div v-if="account.stats.avg_duration_ms != null">
            <div class="text-xs opacity-55">平均请求耗时</div>
            <div class="mt-1 text-xl font-semibold tabular-nums">
              {{ formatCount(account.stats.avg_duration_ms, 0) }} ms
            </div>
          </div>
        </div>
        <div
          v-if="
            account.stats.standard_cost_usd != null ||
            account.stats.user_cost_usd != null ||
            account.stats.today
          "
          class="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-xs opacity-60"
        >
          <span v-if="account.stats.standard_cost_usd != null">
            标准成本 {{ formatCurrency(account.stats.standard_cost_usd) }}
          </span>
          <span v-if="account.stats.user_cost_usd != null">
            用户扣费 {{ formatCurrency(account.stats.user_cost_usd) }}
          </span>
          <span v-if="account.stats.today?.request_count != null">
            今日 {{ formatCount(account.stats.today.request_count) }} 次请求
          </span>
          <span v-if="account.stats.today?.token_count != null">
            今日 {{ formatCount(account.stats.today.token_count) }} Token
          </span>
          <span v-if="account.stats.today?.account_cost_usd != null">
            今日账号成本
            {{ formatCurrency(account.stats.today.account_cost_usd) }}
          </span>
        </div>
      </section>

      <section
        v-if="hasRuntimeDetails(account)"
        class="border-t border-base-300 pt-5"
      >
        <h3 class="mb-3 font-semibold">运行状态</h3>
        <dl class="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
          <div v-if="account.usage?.five_hour?.used_percent != null">
            <dt class="text-xs opacity-50">5 小时窗口</dt>
            <dd class="mt-1 font-medium tabular-nums">
              {{ formatPercent(account.usage.five_hour.used_percent) }}
              <span
                v-if="account.usage.five_hour.reset_at"
                class="block text-xs font-normal opacity-55"
              >
                {{ dateTime(account.usage.five_hour.reset_at) }} 重置
              </span>
            </dd>
          </div>
          <div v-if="account.runtime?.current_concurrency != null">
            <dt class="text-xs opacity-50">当前并发</dt>
            <dd class="mt-1 font-medium tabular-nums">
              {{ account.runtime.current_concurrency }}
              <template v-if="account.runtime.concurrency_limit != null">
                / {{ account.runtime.concurrency_limit }}
              </template>
            </dd>
          </div>
          <div v-if="account.runtime?.last_used_at">
            <dt class="text-xs opacity-50">最近使用</dt>
            <dd class="mt-1 font-medium">
              {{ dateTime(account.runtime.last_used_at) }}
            </dd>
          </div>
          <div v-if="account.runtime?.rate_limited_at">
            <dt class="text-xs opacity-50">发生限流</dt>
            <dd class="mt-1 font-medium">
              {{ dateTime(account.runtime.rate_limited_at) }}
            </dd>
          </div>
          <div v-if="account.runtime?.rate_limit_reset_at">
            <dt class="text-xs opacity-50">限流解除</dt>
            <dd class="mt-1 font-medium">
              {{ dateTime(account.runtime.rate_limit_reset_at) }}
            </dd>
          </div>
          <div v-if="account.runtime?.overload_until">
            <dt class="text-xs opacity-50">过载至</dt>
            <dd class="mt-1 font-medium">
              {{ dateTime(account.runtime.overload_until) }}
            </dd>
          </div>
          <div v-if="account.runtime?.temp_unschedulable_until">
            <dt class="text-xs opacity-50">暂停调度至</dt>
            <dd class="mt-1 font-medium">
              {{ dateTime(account.runtime.temp_unschedulable_until) }}
            </dd>
          </div>
        </dl>
        <div class="mt-3 flex flex-wrap gap-2">
          <span v-if="account.usage?.needs_reauth" class="badge badge-error">
            需要重新授权
          </span>
          <span v-if="account.usage?.needs_verify" class="badge badge-warning">
            需要人工验证
          </span>
          <span v-if="account.usage?.is_banned" class="badge badge-error">
            账号已封禁
          </span>
        </div>
        <p
          v-if="
            account.runtime?.error_message ||
            account.runtime?.temp_unschedulable_reason
          "
          class="mt-3 rounded-box bg-error/10 px-3 py-2 text-sm text-error"
        >
          {{
            account.runtime.error_message ||
            account.runtime.temp_unschedulable_reason
          }}
        </p>
      </section>

      <div
        v-for="warning in account.warnings"
        :key="warning"
        class="alert py-2 text-sm alert-warning"
      >
        <AppIcon name="exclamation-triangle" class="size-4" />
        <span>{{ warning }}</span>
      </div>
    </div>
  </article>
</template>
