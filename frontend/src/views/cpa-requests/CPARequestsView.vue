<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, ApiError } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import { useDateTime, useZonedDateTimeIso } from "@/composables/useDateTime";
import type { MonitoredAccount } from "@/types/accounts";
import type { CPARequest, CPARequests } from "@/types/cpa";
import PageShellHeader from "@/components/common/PageShellHeader.vue";
import CPAModelPricingDialog from "@/components/common/CPAModelPricingDialog.vue";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const formatTime = useDateTime();
const toIso = useZonedDateTimeIso();
const accounts = ref<MonitoredAccount[]>([]);
const accountId = ref<number | null>(null);
const initialized = ref(false);
const loading = ref(false);
const error = ref("");
const data = ref<CPARequests | null>(null);
const defaults = () => ({
  key: "",
  model: "",
  status: "",
  range: "7",
  start: "",
  end: "",
});
const filters = ref(defaults());
const applied = ref(defaults());
const page = ref(1);
const pageSize = ref(25);
const selection = ref<CPARequest | null>(null);
const details = ref<HTMLDialogElement | null>(null);
const pricing = ref<InstanceType<typeof CPAModelPricingDialog> | null>(null);
const summary = computed(() => data.value?.summary);
const pageCount = computed(() =>
  Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize.value)),
);
const dirty = computed(
  () => JSON.stringify(filters.value) !== JSON.stringify(applied.value),
);
let generation = 0;
const compact = (n: number) =>
  new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
const money = (n: number) => `$${n.toFixed(n > 0 && n < 0.01 ? 4 : 2)}`;
const duration = (ms: number | null | undefined) =>
  ms && ms > 0 ? `${(ms / 1000).toFixed(1)} s` : "—";
const service = (item: CPARequest) =>
  item.response_service_tier || item.requested_service_tier || "标准";
const speed = (item: CPARequest) =>
  item.latency_ms > item.ttft_ms && item.ttft_ms > 0 && item.output_tokens > 0
    ? (item.output_tokens / ((item.latency_ms - item.ttft_ms) / 1000)).toFixed(
        1,
      )
    : "—";
const metrics = computed(() => {
  const s = summary.value;
  return [
    {
      label: "请求总数",
      value: s ? s.request_count.toLocaleString() : "—",
      note: s ? `${s.failed_count} 次失败` : "筛选范围内的全部请求",
      icon: "chat-bubble-left-right",
      tone: "text-info",
    },
    {
      label: "成功率",
      value: s?.request_count
        ? `${((1 - s.failed_count / s.request_count) * 100).toFixed(1)}%`
        : "—",
      note: "按请求成功 / 失败状态统计",
      icon: "check-circle",
      tone: "text-success",
    },
    {
      label: "平均耗时",
      value: duration(s?.average_latency_ms),
      note: `平均首 Token ${duration(s?.average_ttft_ms)}`,
      icon: "clock",
      tone: "text-warning",
    },
    {
      label: "估算费用",
      value: s ? money(s.usage_usd) : "—",
      note: s?.unpriced_request_count
        ? `${s.unpriced_request_count} 次缺价，尚未计入`
        : "按已配置模型价格估算",
      icon: "banknotes",
      tone: "text-accent",
    },
    {
      label: "总 Token",
      value: s ? compact(s.total_tokens) : "—",
      note: `推理 ${s ? compact(s.reasoning_tokens) : "—"} Token`,
      icon: "cpu-chip",
      tone: "text-secondary",
    },
    {
      label: "输入",
      value: s ? compact(s.input_tokens) : "—",
      note: "包含缓存输入 Token",
      icon: "arrow-down-tray",
      tone: "text-info",
    },
    {
      label: "输出",
      value: s ? compact(s.output_tokens) : "—",
      note: "模型输出 Token",
      icon: "arrow-up-tray",
      tone: "text-secondary",
    },
    {
      label: "缓存输入",
      value: s ? compact(s.cached_input_tokens) : "—",
      note: `命中率 ${s?.input_tokens ? `${((s.cached_input_tokens / s.input_tokens) * 100).toFixed(1)}%` : "—"}`,
      icon: "circle-stack",
      tone: "text-accent",
    },
  ];
});

async function load() {
  if (accountId.value == null) return;
  const current = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const f = applied.value;
    const query = new URLSearchParams({
      account_id: String(accountId.value),
      page: String(page.value),
      page_size: String(pageSize.value),
      include_summary: "true",
    });
    if (f.key) query.set("key_id", f.key);
    if (f.model) query.set("model", f.model);
    if (f.status) query.set("failed", f.status);
    if (f.range === "custom") {
      if (!f.start || !f.end) throw new Error("请选择完整的开始和结束时间");
      const start = toIso(f.start),
        end = toIso(f.end);
      if (
        Date.parse(start) >= Date.parse(end) ||
        Date.parse(end) - Date.parse(start) > 90 * 86400000
      )
        throw new Error("时间范围须大于零且不超过 90 天");
      query.set("started_at", start);
      query.set("ended_at", end);
    } else query.set("days", f.range);
    const result = await api<CPARequests>(`cpa/requests?${query}`);
    if (current === generation) data.value = result;
  } catch (reason) {
    if (current === generation) {
      data.value = null;
      error.value =
        reason instanceof Error ? reason.message : "请求明细加载失败";
    }
  } finally {
    if (current === generation) loading.value = false;
  }
}
function search() {
  applied.value = { ...filters.value };
  page.value = 1;
  void load();
}
function reset() {
  filters.value = defaults();
  search();
}
function paginate(delta: number) {
  page.value += delta;
  void load();
}
function showDetails(item: CPARequest) {
  selection.value = item;
  details.value?.showModal();
}
function selectAccount() {
  generation++;
  data.value = null;
  filters.value = defaults();
  applied.value = defaults();
  page.value = 1;
  void load();
}
watch(accountId, (id) => {
  if (!initialized.value) return;
  void router.replace({ query: { account_id: id } });
  selectAccount();
});
watch(
  () => route.query.account_id,
  (id) => {
    if (accounts.value.some((a) => a.id === Number(id)))
      accountId.value = Number(id);
  },
);
onMounted(async () => {
  try {
    accounts.value = (
      await api<MonitoredAccount[]>("settings/monitored-accounts")
    ).filter((a) => a.provider === "cpa");
    accountId.value =
      accounts.value.find((a) => a.id === Number(route.query.account_id))?.id ??
      accounts.value[0]?.id ??
      null;
    initialized.value = true;
    await load();
  } catch (reason) {
    initialized.value = true;
    error.value = reason instanceof ApiError ? reason.message : "账号加载失败";
  }
});
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="mb-2 flex items-center gap-2">
        <span class="badge badge-outline badge-sm">CPA</span
        ><span class="text-sm text-base-content/60">请求与用量</span>
      </div>
      <h1 class="text-2xl font-semibold tracking-tight">
        {{ auth.isStaff ? "CPA 请求明细" : "我的 CPA 请求" }}
      </h1>
      <p class="mt-2 text-sm text-base-content/60">
        每一次调用的模型、用量与响应表现。{{
          auth.isStaff
            ? "查看已授权账号的采集记录。"
            : "仅展示本人获授权参与者的请求。"
        }}
      </p>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <select
        v-if="accounts.length"
        v-model="accountId"
        aria-label="选择 CPA 账号"
        class="select w-full sm:w-56"
        :disabled="loading"
      >
        <option
          v-for="account in accounts"
          :key="account.id"
          :value="account.id"
        >
          {{ account.name }}
        </option>
      </select>
      <button
        v-if="auth.isStaff && accountId"
        class="btn"
        @click="pricing?.open()"
      >
        <AppIcon name="adjustments-horizontal" class="size-4" />模型价格
      </button>
      <button class="btn" :disabled="loading || !accountId" @click="load">
        <AppIcon
          name="arrow-path"
          class="size-4"
          :class="{ 'animate-spin': loading }"
        />刷新
      </button>
    </div>
  </PageShellHeader>
  <div v-if="error" role="alert" class="col-span-12 alert alert-error">
    {{ error }}
  </div>
  <div
    v-if="initialized && !accounts.length && !error"
    class="card col-span-12 bg-base-200 card-border"
  >
    <div class="card-body items-center py-16 text-center">
      <AppIcon name="inbox" class="size-10 text-base-content/40" />
      <h2 class="card-title">暂无可查看的 CPA 账号</h2>
      <p>
        {{
          auth.isStaff
            ? "请先在系统设置中添加 CPA 监控账号。"
            : "请联系管理员授权 CPA 账号并配置参与者和额度池。"
        }}
      </p>
      <RouterLink v-if="auth.isStaff" to="/settings" class="btn"
        >前往系统设置</RouterLink
      >
    </div>
  </div>
  <template v-if="accountId">
    <form
      class="card col-span-12 bg-base-200 card-border"
      @submit.prevent="search"
    >
      <div class="card-body gap-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="flex items-center gap-2 font-medium">
            <AppIcon name="funnel" class="size-4" />筛选请求
          </h2>
          <span class="text-xs text-base-content/60"
            >时间按 {{ auth.timezone }} 显示 · 最多查询 90 天</span
          >
        </div>
        <div class="grid grid-cols-2 gap-3 xl:grid-cols-5">
          <label class="grid gap-2 text-xs text-base-content/70"
            >时间范围<select v-model="filters.range" class="select w-full">
              <option value="1">最近 24 小时</option>
              <option value="7">最近 7 天</option>
              <option value="30">最近 30 天</option>
              <option value="custom">自定义时间</option>
            </select></label
          >
          <label class="grid gap-2 text-xs text-base-content/70"
            >模型<select v-model="filters.model" class="select w-full">
              <option value="">全部模型</option>
              <option v-for="model in data?.models" :key="model">
                {{ model }}
              </option>
            </select></label
          >
          <label class="grid gap-2 text-xs text-base-content/70"
            >API Key<select v-model="filters.key" class="select w-full">
              <option value="">全部可查看 Key</option>
              <option
                v-for="key in data?.keys"
                :key="key.id"
                :value="String(key.id)"
              >
                {{ key.name || "API Key" }} ····{{ key.hint }}
              </option>
            </select></label
          >
          <label class="grid gap-2 text-xs text-base-content/70"
            >请求状态<select v-model="filters.status" class="select w-full">
              <option value="">全部状态</option>
              <option value="false">仅成功</option>
              <option value="true">仅失败</option>
            </select></label
          >
          <div class="col-span-2 flex items-end gap-2 xl:col-span-1">
            <button
              type="submit"
              class="btn flex-1 btn-primary"
              :disabled="loading"
            >
              查询</button
            ><button
              type="button"
              class="btn btn-ghost"
              :disabled="loading"
              @click="reset"
            >
              重置
            </button>
          </div>
        </div>
        <div
          v-if="filters.range === 'custom'"
          class="grid gap-3 sm:grid-cols-2"
        >
          <label class="grid gap-2 text-xs"
            >开始时间<input
              v-model="filters.start"
              type="datetime-local"
              class="input w-full" /></label
          ><label class="grid gap-2 text-xs"
            >结束时间<input
              v-model="filters.end"
              type="datetime-local"
              class="input w-full"
          /></label>
        </div>
        <p v-if="dirty" class="text-xs text-warning" role="status">
          筛选条件已修改，点击查询后更新结果。
        </p>
      </div>
    </form>
    <div
      class="col-span-12 flex flex-wrap items-center justify-between gap-2 text-xs text-base-content/60"
    >
      <span>{{
        data
          ? `${formatTime(data.started_at)} — ${formatTime(data.ended_at)}`
          : "正在读取请求范围…"
      }}</span
      ><span role="status"
        >{{
          loading
            ? "正在更新…"
            : data
              ? `更新于 ${formatTime(data.generated_at)}`
              : "等待查询"
        }}
        · 摘要覆盖全部筛选结果</span
      >
    </div>
    <div
      class="col-span-12 grid grid-cols-2 gap-3 xl:grid-cols-4"
      :aria-busy="loading"
    >
      <div
        v-for="metric in metrics"
        :key="metric.label"
        class="stats min-w-0 border border-base-300 bg-base-200 shadow-xs"
      >
        <div class="stat min-w-0 gap-2 p-4 sm:p-5">
          <div class="stat-title flex items-center gap-2 text-xs sm:text-sm">
            <AppIcon
              :name="metric.icon"
              class="size-5 shrink-0"
              :class="metric.tone"
            />{{ metric.label }}
          </div>
          <div class="stat-value text-2xl tabular-nums sm:text-3xl">
            {{ metric.value }}
          </div>
          <div class="stat-desc text-xs whitespace-normal">
            {{ metric.note }}
          </div>
        </div>
      </div>
    </div>
    <div
      v-if="summary?.unpriced_request_count"
      class="col-span-12 alert alert-warning"
      role="status"
    >
      <AppIcon name="exclamation-triangle" class="size-5" /><span
        >{{
          summary.unpriced_request_count
        }}
        次请求缺少模型价格，费用暂未计入。{{
          auth.isStaff
            ? "可一键补齐已收录模型的价格。"
            : "请联系管理员补齐价格。"
        }}</span
      ><button
        v-if="auth.isStaff"
        class="btn btn-sm"
        :disabled="loading"
        @click="pricing?.open(true)"
      >
        一键同步缺失价格
      </button>
    </div>
    <section
      class="card col-span-12 min-w-0 bg-base-200 card-border"
      data-testid="cpa-requests"
      :aria-busy="loading"
    >
      <div class="card-body gap-5">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-center gap-3">
            <h2 class="card-title">请求记录</h2>
            <span class="badge badge-ghost"
              >{{ data?.total.toLocaleString() ?? "—" }} 条</span
            >
          </div>
          <p class="text-xs text-base-content/60">
            最新请求在前 · 费用为估算值
          </p>
        </div>
        <div class="hidden overflow-x-auto lg:block">
          <table class="table table-zebra">
            <thead>
              <tr>
                <th>模型 / 服务</th>
                <th>状态</th>
                <th>Token 用量</th>
                <th>缓存输入</th>
                <th>响应表现</th>
                <th>估算费用</th>
                <th>请求时间</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in data?.items"
                :key="item.id"
                class="hover:bg-base-300/40"
              >
                <td class="max-w-64 py-5">
                  <div class="truncate font-medium" :title="item.model">
                    {{ item.model }}
                  </div>
                  <div class="mt-1 text-xs text-base-content/60">
                    {{ service(item) }} · Key ····{{
                      item.api_key_hint || "未知"
                    }}
                  </div>
                </td>
                <td>
                  <span
                    class="badge badge-soft badge-sm text-base-content!"
                    :class="item.failed ? 'badge-error' : 'badge-success'"
                    >{{ item.failed ? "失败" : "成功" }}</span
                  >
                </td>
                <td class="tabular-nums">
                  <div
                    class="font-medium"
                    :title="item.total_tokens.toLocaleString()"
                  >
                    {{ compact(item.total_tokens) }}
                  </div>
                  <div
                    class="mt-1 text-xs whitespace-nowrap text-base-content/60"
                  >
                    入 {{ compact(item.input_tokens) }} · 出
                    {{ compact(item.output_tokens) }}
                  </div>
                </td>
                <td class="tabular-nums">
                  <div>{{ compact(item.cached_input_tokens) }}</div>
                  <div class="mt-1 text-xs text-base-content/60">
                    {{
                      item.input_tokens
                        ? (
                            (item.cached_input_tokens / item.input_tokens) *
                            100
                          ).toFixed(1)
                        : "0.0"
                    }}% 命中
                  </div>
                </td>
                <td class="text-xs whitespace-nowrap tabular-nums">
                  <div>
                    首 Token
                    <span class="font-medium">{{
                      duration(item.ttft_ms)
                    }}</span>
                  </div>
                  <div class="mt-1 text-base-content/60">
                    总耗时 {{ duration(item.latency_ms) }}
                  </div>
                </td>
                <td class="font-medium tabular-nums">
                  <span
                    v-if="item.unpriced"
                    class="badge badge-soft badge-sm badge-warning"
                    >未计价</span
                  ><template v-else>{{ money(item.usage_usd) }}</template>
                </td>
                <td class="text-xs whitespace-nowrap text-base-content/70">
                  {{ formatTime(item.occurred_at) }}
                </td>
                <td>
                  <button
                    class="btn btn-ghost btn-sm"
                    :aria-label="`查看请求 ${item.request_id || item.id}`"
                    @click="showDetails(item)"
                  >
                    详情
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="grid gap-3 lg:hidden">
          <article
            v-for="item in data?.items"
            :key="item.id"
            class="card bg-base-100 card-border"
          >
            <div class="card-body gap-3 p-4">
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                  <h3 class="truncate font-medium">{{ item.model }}</h3>
                  <p class="mt-1 text-xs text-base-content/60">
                    Key ····{{ item.api_key_hint || "未知" }} ·
                    {{ service(item) }}
                  </p>
                </div>
                <span
                  class="badge badge-soft badge-sm text-base-content!"
                  :class="item.failed ? 'badge-error' : 'badge-success'"
                  >{{ item.failed ? "失败" : "成功" }}</span
                >
              </div>
              <div class="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p class="text-xs text-base-content/60">Token / 估算费用</p>
                  <p class="mt-1 tabular-nums">
                    {{ compact(item.total_tokens) }} ·
                    {{ item.unpriced ? "未计价" : money(item.usage_usd) }}
                  </p>
                </div>
                <div>
                  <p class="text-xs text-base-content/60">首 Token / 总耗时</p>
                  <p class="mt-1 tabular-nums">
                    {{ duration(item.ttft_ms) }} /
                    {{ duration(item.latency_ms) }}
                  </p>
                </div>
              </div>
              <div class="flex items-center justify-between gap-2">
                <time class="text-xs text-base-content/60">{{
                  formatTime(item.occurred_at)
                }}</time
                ><button
                  class="btn btn-ghost btn-sm"
                  :aria-label="`查看请求 ${item.request_id || item.id}`"
                  @click="showDetails(item)"
                >
                  详情
                </button>
              </div>
            </div>
          </article>
        </div>
        <div
          v-if="loading && !data"
          class="flex justify-center py-16"
          role="status"
        >
          <span class="loading loading-spinner"></span
          ><span class="ml-3">正在读取请求…</span>
        </div>
        <div
          v-else-if="!loading && !data?.items.length"
          class="flex flex-col items-center gap-3 py-12 text-center"
        >
          <AppIcon
            name="magnifying-glass"
            class="size-9 text-base-content/40"
          />
          <h3 class="font-medium">
            {{ error ? "请求暂时无法加载" : "没有符合条件的请求" }}
          </h3>
          <p class="text-sm text-base-content/60">
            {{
              error
                ? "请稍后重试。"
                : "试试扩大时间范围，或清除模型、Key 和状态筛选。"
            }}
          </p>
          <button class="btn btn-sm" @click="reset">清除筛选并重试</button>
        </div>
        <div
          class="flex flex-wrap items-center justify-between gap-3 border-t border-base-300 pt-4"
        >
          <label class="flex items-center gap-2 text-xs text-base-content/60"
            >每页<select
              v-model="pageSize"
              class="select w-20 select-sm"
              :disabled="loading"
              @change="
                page = 1;
                load();
              "
            >
              <option :value="25">25</option>
              <option :value="50">50</option>
              <option :value="100">100</option></select
            >条</label
          >
          <div class="flex items-center gap-3">
            <span class="text-xs tabular-nums"
              >{{ page }} / {{ pageCount }} 页</span
            ><button
              class="btn btn-sm"
              :disabled="loading || page <= 1"
              @click="paginate(-1)"
            >
              上一页</button
            ><button
              class="btn btn-sm"
              :disabled="loading || page >= pageCount"
              @click="paginate(1)"
            >
              下一页
            </button>
          </div>
        </div>
      </div>
    </section>
  </template>
  <CPAModelPricingDialog
    v-if="auth.isStaff && accountId"
    ref="pricing"
    :account-id="accountId"
    @saved="load"
  />
  <dialog ref="details" class="modal" aria-labelledby="request-detail-title">
    <div class="modal-box max-w-2xl">
      <div class="flex items-center justify-between gap-4">
        <h2 id="request-detail-title" class="text-lg font-semibold">
          单次请求详情
        </h2>
        <form method="dialog">
          <button class="btn btn-circle btn-ghost btn-sm" aria-label="关闭详情">
            <AppIcon name="x-mark" class="size-5" />
          </button>
        </form>
      </div>
      <template v-if="selection"
        ><div class="mt-5 flex items-center gap-3">
          <h3 class="text-xl font-medium break-all">{{ selection.model }}</h3>
          <span
            class="badge badge-soft text-base-content!"
            :class="selection.failed ? 'badge-error' : 'badge-success'"
            >{{ selection.failed ? "失败" : "成功" }}</span
          >
        </div>
        <p class="mt-2 text-sm text-base-content/60">
          {{ formatTime(selection.occurred_at) }} · Key ····{{
            selection.api_key_hint || "未知"
          }}
        </p>
        <dl class="mt-6 grid grid-cols-2 gap-5 text-sm">
          <div
            v-for="entry in [
              ['输入 Token', selection.input_tokens.toLocaleString()],
              [
                '缓存输入 Token',
                selection.cached_input_tokens.toLocaleString(),
              ],
              ['输出 Token', selection.output_tokens.toLocaleString()],
              ['推理 Token', selection.reasoning_tokens.toLocaleString()],
              ['总耗时', duration(selection.latency_ms)],
              ['首 Token 延迟', duration(selection.ttft_ms)],
              ['估算输出速度', `${speed(selection)} Token/s`],
              [
                '估算费用',
                selection.unpriced ? '未计价' : money(selection.usage_usd),
              ],
              ['请求服务等级', selection.requested_service_tier || '未提供'],
              ['响应服务等级', selection.response_service_tier || '未提供'],
            ]"
            :key="entry[0]"
          >
            <dt class="text-base-content/60">{{ entry[0] }}</dt>
            <dd class="mt-1 font-medium tabular-nums">{{ entry[1] }}</dd>
          </div>
          <div class="col-span-2">
            <dt class="text-base-content/60">请求标识</dt>
            <dd
              class="mt-2 rounded-box bg-base-300/40 p-3 font-mono text-xs break-all select-all"
            >
              {{ selection.request_id || `#${selection.id}` }}
            </dd>
          </div>
          <div class="col-span-2">
            <dt class="text-base-content/60">端点</dt>
            <dd class="mt-1 font-mono text-xs break-all">
              {{ selection.endpoint || "未提供" }}
            </dd>
          </div>
        </dl>
        <p class="mt-5 text-xs leading-5 text-base-content/60">
          费用按当前模型价格估算；输出速度按输出 Token ÷（总耗时 − 首 Token
          延迟）估算，缺少有效耗时时不展示。不采集或展示对话正文。
        </p></template
      >
    </div>
    <form method="dialog" class="modal-backdrop">
      <button>关闭详情</button>
    </form>
  </dialog>
</template>
