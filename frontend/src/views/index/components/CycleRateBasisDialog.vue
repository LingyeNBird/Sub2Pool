<script setup lang="ts">
import { ref } from "vue";

import CalculationBasisHeader from "@/components/common/CalculationBasisHeader.vue";
import CalculationBasisTimeline from "@/components/common/CalculationBasisTimeline.vue";
import { useDateTime } from "@/composables/useDateTime";
import type { DashboardData } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

defineProps<{
  data: DashboardData;
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const dateTime = useDateTime();

function open() {
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
}

defineExpose({ open, close });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div class="modal-box max-w-3xl">
      <form method="dialog">
        <button
          class="btn absolute top-3 right-3 btn-circle btn-ghost btn-sm"
          aria-label="关闭"
        >
          ✕
        </button>
      </form>
      <template v-if="data.cycle?.rate_calculated">
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
          :end-value="`${formatCurrency(
            data.cycle.rate_samples[0].cost_usd,
          )} / ${formatPercent(data.cycle.rate_samples[0].used_percent)}`"
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
            {{ formatCurrency(data.cycle.rate_samples[0].cost_usd) }} ÷
            {{ formatPercent(data.cycle.rate_samples[0].used_percent) }} =
            {{ formatCurrency(data.cycle.rate_samples[0].usd_per_percent) }} /
            1%
          </p>
          <p class="mt-2 text-sm opacity-70">
            最近
            {{ data.cycle.rate_sample_count }} 个有效样本按已用百分比加权，取
            {{ data.cycle.conservative_percentile }}% 保守分位，最终采用
            <strong>{{
              formatCurrency(data.cycle.effective_usd_per_percent)
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
                <td>{{ formatCurrency(sample.cost_usd) }}</td>
                <td>{{ formatPercent(sample.used_percent) }}</td>
                <td>{{ formatCurrency(sample.usd_per_percent) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
      <div class="modal-action">
        <button class="btn" @click="close">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
