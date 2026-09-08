<script setup lang="ts">
import { ref, watch } from "vue";
import { ApiError, api } from "@/services/api";
import { useDateTime, useZonedDateTimeIso } from "@/composables/useDateTime";
import { useAuthStore } from "@/stores/auth";
import type { CPARequests } from "@/types/cpa";
import { formatCurrency } from "@/utils/formatters";

const formatDateTime = useDateTime();
const toIso = useZonedDateTimeIso();
const props = defineProps<{ accountId: number; refreshKey?: number }>();
const auth = useAuthStore();
const data = ref<CPARequests | null>(null);
const loading = ref(false);
const error = ref("");
const keyId = ref("");
const model = ref("");
const failed = ref("");
const startedAt = ref("");
const endedAt = ref("");
const page = ref(1);
let generation = 0;

async function load() {
  const current = ++generation;
  loading.value = true;
  error.value = "";
  const query = new URLSearchParams({
    account_id: String(props.accountId),
    page: String(page.value),
    page_size: "25",
  });
  if (keyId.value) query.set("key_id", keyId.value);
  if (model.value) query.set("model", model.value);
  if (failed.value) query.set("failed", failed.value);
  if (startedAt.value) query.set("started_at", toIso(startedAt.value));
  if (endedAt.value) query.set("ended_at", toIso(endedAt.value));
  try {
    const result = await api<CPARequests>(`cpa/requests?${query}`);
    if (current === generation) data.value = result;
  } catch (reason) {
    if (current === generation) {
      data.value = null;
      error.value =
        reason instanceof ApiError ? reason.message : "请求明细加载失败";
    }
  } finally {
    if (current === generation) loading.value = false;
  }
}
function search() {
  page.value = 1;
  void load();
}
watch(() => props.refreshKey, search);
function paginate(delta: number) {
  page.value += delta;
  void load();
}
watch(
  () => props.accountId,
  () => {
    data.value = null;
    keyId.value = "";
    model.value = "";
    search();
  },
  { immediate: true },
);
</script>

<template>
  <section
    id="cpa-requests"
    tabindex="-1"
    class="card col-span-12 min-w-0 bg-base-200 shadow-xs"
    data-testid="cpa-requests"
  >
    <div class="card-body gap-4">
      <h2 class="card-title">
        {{ auth.isStaff ? "CPA 请求明细" : "我的 CPA 请求" }}
      </h2>
      <p class="text-sm opacity-60">
        默认查询最近 7 天，最多 90 天，时间按
        {{ auth.timezone }} 显示。费用按当前模型价格估算，仅包含已采集的请求。
      </p>
      <form class="flex flex-wrap items-end gap-3" @submit.prevent="search">
        <label class="grid gap-1 text-sm"
          >Key<select v-model="keyId" class="select select-sm">
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
        <label class="grid gap-1 text-sm"
          >模型<select v-model="model" class="select select-sm">
            <option value="">全部模型</option>
            <option v-for="name in data?.models" :key="name">{{ name }}</option>
          </select></label
        >
        <label class="grid gap-1 text-sm"
          >状态<select
            aria-label="状态"
            v-model="failed"
            class="select select-sm"
          >
            <option value="">全部状态</option>
            <option value="false">成功</option>
            <option value="true">失败</option>
          </select></label
        >
        <label class="grid gap-1 text-sm"
          >开始<input
            v-model="startedAt"
            type="datetime-local"
            class="input input-sm"
        /></label>
        <label class="grid gap-1 text-sm"
          >结束<input
            v-model="endedAt"
            type="datetime-local"
            class="input input-sm"
        /></label>
        <button class="btn btn-sm" :disabled="loading">
          {{ loading ? "查询中" : "查询" }}
        </button>
      </form>
      <p v-if="error" role="alert" class="alert">{{ error }}</p>
      <div class="overflow-x-auto">
        <table class="table table-sm">
          <thead>
            <tr>
              <th>时间 / 请求</th>
              <th>模型 / Key</th>
              <th>输入 / 缓存</th>
              <th>输出 / 推理</th>
              <th>总 Token</th>
              <th>估算费用</th>
              <th>状态</th>
              <th>耗时 / 首 Token</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in data?.items" :key="item.id">
              <td>
                {{ formatDateTime(item.occurred_at) }}
                <p
                  class="max-w-64 truncate text-xs opacity-60"
                  :title="item.request_id"
                >
                  {{ item.request_id || `#${item.id}` }}
                </p>
              </td>
              <td>
                {{ item.model }}
                <p class="text-xs opacity-60">
                  ····{{ item.api_key_hint || "未知" }} ·
                  {{
                    item.response_service_tier ||
                    item.requested_service_tier ||
                    "标准"
                  }}
                </p>
              </td>
              <td>
                {{ item.input_tokens.toLocaleString() }} /
                {{ item.cached_input_tokens.toLocaleString() }}
              </td>
              <td>
                {{ item.output_tokens.toLocaleString() }} /
                {{ item.reasoning_tokens.toLocaleString() }}
              </td>
              <td>{{ item.total_tokens.toLocaleString() }}</td>
              <td>
                {{ item.unpriced ? "未计价" : formatCurrency(item.usage_usd) }}
              </td>
              <td>
                <span
                  class="badge badge-sm"
                  :class="item.failed ? 'badge-warning' : 'badge-outline'"
                  >{{ item.failed ? "失败" : "成功" }}</span
                >
              </td>
              <td>{{ item.latency_ms }} / {{ item.ttft_ms }} ms</td>
            </tr>
            <tr v-if="!loading && !data?.items.length">
              <td colspan="8">此范围内暂无可查看的请求。</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="flex flex-wrap items-center justify-between gap-2">
        <p class="text-sm opacity-60">
          共 {{ data?.total ?? 0 }} 条 · 第 {{ page }} 页
        </p>
        <div class="flex gap-2">
          <button
            class="btn btn-sm"
            :disabled="loading || page <= 1"
            @click="paginate(-1)"
          >
            上一页</button
          ><button
            class="btn btn-sm"
            :disabled="loading || page * 25 >= (data?.total ?? 0)"
            @click="paginate(1)"
          >
            下一页
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
