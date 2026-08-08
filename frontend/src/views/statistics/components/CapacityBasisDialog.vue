<script setup lang="ts">
import { ref } from "vue";

import CalculationBasisHeader from "@/components/common/CalculationBasisHeader.vue";
import CalculationBasisTimeline from "@/components/common/CalculationBasisTimeline.vue";
import { useDateTime } from "@/composables/useDateTime";
import type { StatisticsData } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

defineProps<{
  data: StatisticsData;
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const basisKind = ref<"cycle" | "today">("cycle");
const dateTime = useDateTime();

function open(kind: "cycle" | "today") {
  basisKind.value = kind;
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

      <template
        v-if="
          basisKind === 'cycle' && data.capacity_summary.cycle?.rate_calculated
        "
      >
        <CalculationBasisHeader
          title="本周期累计折算依据"
          help="先用本周期累计成本与官方已用百分比形成样本，再按设置的保守分位采用结果。"
        />
        <CalculationBasisTimeline
          :start-time="dateTime(data.capacity_summary.cycle.starts_at)"
          :start-value="`${formatCurrency(
            data.capacity_summary.cycle.start_cost_usd,
          )} / ${formatPercent(data.capacity_summary.cycle.start_percent)}`"
          :end-time="dateTime(data.capacity_summary.cycle.observed_at)"
          :end-value="`${formatCurrency(
            data.capacity_summary.cycle.end_cost_usd,
          )} / ${formatPercent(data.capacity_summary.cycle.end_percent)}`"
        />
        <div class="mt-3 rounded-box border border-base-300 p-4">
          <div class="text-center text-sm font-semibold opacity-60">
            累计样本公式
          </div>
          <p
            class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
          >
            ({{ formatCurrency(data.capacity_summary.cycle.end_cost_usd) }} −
            {{ formatCurrency(data.capacity_summary.cycle.start_cost_usd) }}) ÷
            ({{ formatPercent(data.capacity_summary.cycle.end_percent) }} −
            {{ formatPercent(data.capacity_summary.cycle.start_percent) }}) ×
            100 =
            {{ formatCurrency(data.capacity_summary.cycle.raw_estimate_usd) }}
          </p>
          <p class="mt-2 text-sm opacity-70">
            最近 {{ data.capacity_summary.cycle.rate_sample_count }}
            个有效累计样本按已用百分比加权，取
            {{ data.capacity_summary.cycle.conservative_percentile }}%
            保守分位；采用
            {{
              formatCurrency(
                data.capacity_summary.cycle.effective_usd_per_percent,
              )
            }}
            / 1%，最终为
            <strong>{{
              formatCurrency(data.capacity_summary.cycle.estimate_usd)
            }}</strong>
            。
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
                v-for="sample in data.capacity_summary.cycle.rate_samples"
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

      <template
        v-else-if="
          basisKind === 'today' && data.capacity_summary.today.sufficient
        "
      >
        <CalculationBasisHeader
          title="今日用量折算依据"
          help="只比较今天首个和末个采样点，减少整数百分比在短间隔内造成的误差。"
        />
        <CalculationBasisTimeline
          :start-time="dateTime(data.capacity_summary.today.observed_from)"
          :start-value="`${formatCurrency(
            data.capacity_summary.today.start_cost_usd,
          )} / ${formatPercent(data.capacity_summary.today.start_percent)}`"
          :end-time="dateTime(data.capacity_summary.today.observed_to)"
          :end-value="`${formatCurrency(
            data.capacity_summary.today.end_cost_usd,
          )} / ${formatPercent(data.capacity_summary.today.end_percent)}`"
        />
        <div class="mt-3 rounded-box border border-base-300 p-4">
          <div class="text-center text-sm font-semibold opacity-60">
            日内增量公式
          </div>
          <p
            class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
          >
            ({{ formatCurrency(data.capacity_summary.today.end_cost_usd) }} −
            {{ formatCurrency(data.capacity_summary.today.start_cost_usd) }}) ÷
            ({{ formatPercent(data.capacity_summary.today.end_percent) }} −
            {{ formatPercent(data.capacity_summary.today.start_percent) }}) ×
            100 = {{ formatCurrency(data.capacity_summary.today.estimate_usd) }}
          </p>
          <p class="mt-2 text-sm opacity-70">
            端点百分比是整数，误差区间约为
            {{ formatCurrency(data.capacity_summary.today.minimum_usd) }} 至
            {{ formatCurrency(data.capacity_summary.today.maximum_usd) }}。
          </p>
        </div>
      </template>

      <div class="modal-action">
        <button class="btn" @click="close">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
