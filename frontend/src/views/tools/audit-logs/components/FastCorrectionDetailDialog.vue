<script setup lang="ts">
import { ref } from "vue";
import CorrectionAmount from "@/components/common/CorrectionAmount.vue";
import CorrectionDetails from "@/components/common/CorrectionDetails.vue";
import { useDateTime } from "@/composables/useDateTime";
import { ApiError, api } from "@/services/api";
import type { FastCorrectionDetail, Observation } from "@/types/observations";
import { formatCurrency } from "@/utils/formatters";
import type { DialogController } from "../types";

const dialog = ref<HTMLDialogElement | null>(null);
const data = ref<FastCorrectionDetail | null>(null);
const loading = ref(false);
const message = ref("");
const dateTime = useDateTime();
let requestVersion = 0;
const evidenceLabels: Record<string, string> = {
  upstream_flag: "上游实际计费标记",
  input_tokens_threshold: "输入 Token 阈值",
  missing_input_facts: "缺少判断事实",
  not_matched: "未命中或未启用",
};
function requestCount(value: number | null) {
  return value === null ? "未知" : String(value);
}
async function open(observation: Observation) {
  const version = ++requestVersion;
  data.value = null;
  message.value = "";
  loading.value = true;
  if (!dialog.value?.open) dialog.value?.showModal();
  try {
    const result = await api<FastCorrectionDetail>(
      `observations/${observation.id}/fast-correction`,
    );
    if (version === requestVersion) data.value = result;
  } catch (error) {
    if (version === requestVersion)
      message.value =
        error instanceof ApiError ? error.message : "加载修正合计明细失败";
  } finally {
    if (version === requestVersion) loading.value = false;
  }
}
function close() {
  ++requestVersion;
  dialog.value?.close();
}
defineExpose<DialogController<[Observation]>>({ open, close });
</script>

<template>
  <dialog
    ref="dialog"
    class="modal"
    aria-labelledby="observation-correction-title"
  >
    <div class="modal-box max-w-5xl">
      <form method="dialog">
        <button
          class="btn absolute top-3 right-3 btn-circle btn-ghost btn-sm"
          aria-label="关闭"
        >
          ✕
        </button>
      </form>
      <h2 id="observation-correction-title" class="pr-10 text-lg font-bold">
        修正合计明细
      </h2>
      <div v-if="loading" class="flex justify-center py-12">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div v-else-if="message" class="mt-4 alert alert-error" role="alert">
        <span>{{ message }}</span>
      </div>
      <template v-else-if="data">
        <p class="mt-1 text-sm opacity-60">
          {{ dateTime(data.started_at) }} 至 {{ dateTime(data.ended_at) }} ·
          {{ data.cost_basis_label }}口径
        </p>
        <div
          v-if="data.collection_error"
          class="mt-4 alert text-sm alert-error"
        >
          <span>{{ data.collection_error }}</span>
        </div>
        <div
          class="stats my-4 w-full stats-vertical bg-base-200 lg:stats-horizontal"
        >
          <div class="stat">
            <div class="stat-title">区间请求（FAST / 非 FAST）</div>
            <div class="stat-value text-xl">
              {{ requestCount(data.request_count) }}
            </div>
            <div class="stat-desc">
              {{ data.fast_request_count }} /
              {{ requestCount(data.non_fast_request_count) }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-title">全部请求原成本</div>
            <div class="stat-value text-xl">
              {{ formatCurrency(data.raw_cost_usd) }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-title">修正后成本</div>
            <div class="stat-value text-xl">
              {{ formatCurrency(data.corrected_cost_usd) }}
            </div>
          </div>
        </div>
        <CorrectionDetails :breakdown="data" />
        <p class="mt-3 text-sm opacity-65">
          有完整原始事实的区间按当前设置重算；修改规则不需要重新请求上游。未保存完整事实的旧记录保持可见并明确标记。
        </p>
        <div class="mt-4 overflow-x-auto">
          <table class="table table-sm">
            <thead>
              <tr>
                <th>Sub2API 用户</th>
                <th>全部请求</th>
                <th>原成本</th>
                <th>修正合计</th>
                <th>修正后成本</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in data.users" :key="item.sub2api_user_id">
                <td>
                  <div class="font-medium">{{ item.display_name }}</div>
                  <div class="text-xs opacity-60">
                    ID {{ item.sub2api_user_id }}
                  </div>
                </td>
                <td>{{ requestCount(item.request_count) }}</td>
                <td class="tabular-nums">
                  {{ formatCurrency(item.raw_cost_usd) }}
                </td>
                <td>
                  <CorrectionAmount
                    :breakdown="{
                      ...item,
                      correction_facts_complete: data.correction_facts_complete,
                    }"
                  />
                </td>
                <td class="tabular-nums">
                  {{ formatCurrency(item.corrected_cost_usd) }}
                </td>
              </tr>
              <tr v-if="data.users.length === 0">
                <td colspan="5" class="py-6 text-center opacity-60">
                  没有可展示的用户请求事实
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <details
          v-if="data.model_details?.length"
          class="collapse-arrow collapse mt-4 bg-base-200"
        >
          <summary class="collapse-title font-medium">
            模型匹配与倍率追溯
          </summary>
          <div class="collapse-content overflow-x-auto">
            <table class="table table-sm">
              <thead>
                <tr>
                  <th>模型 / 档位</th>
                  <th>请求数</th>
                  <th>FAST × 长上下文 × 模型</th>
                  <th>长上下文依据</th>
                  <th>修正合计</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in data.model_details" :key="index">
                  <td>
                    <div class="font-mono">{{ item.model || "未知模型" }}</div>
                    <div class="text-xs opacity-60">
                      {{ item.service_tier || "普通" }}
                    </div>
                  </td>
                  <td>{{ item.request_count }}</td>
                  <td class="font-mono">
                    {{ item.fast_factor }} × {{ item.long_context_factor }} ×
                    {{ item.model_factor }}
                  </td>
                  <td>
                    {{
                      evidenceLabels[item.long_context_evidence] ??
                      item.long_context_evidence
                    }}
                  </td>
                  <td><CorrectionAmount :breakdown="item" /></td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
        <details
          v-if="data.rules"
          class="collapse-arrow collapse mt-3 bg-base-200"
        >
          <summary class="collapse-title font-medium">当前规则快照</summary>
          <div class="collapse-content">
            <p class="mb-2 font-mono text-xs break-all opacity-60">
              {{ data.rules_digest }}
            </p>
            <pre class="overflow-x-auto text-xs">{{
              JSON.stringify(data.rules, null, 2)
            }}</pre>
          </div>
        </details>
      </template>
      <div class="modal-action">
        <button class="btn" @click="close">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
