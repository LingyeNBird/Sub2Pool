<script setup lang="ts">
import { ref } from "vue";

import CalculationBasisHeader from "@/components/common/CalculationBasisHeader.vue";
import CalculationBasisTimeline from "@/components/common/CalculationBasisTimeline.vue";
import CostBreakdownValue from "@/components/common/CostBreakdownValue.vue";
import { useDateTime } from "@/composables/useDateTime";
import type { StatisticsData } from "@/types/statistics";
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
          help="该指标只使用当前归属区间起点与最新观测的累计成本、累计整数百分比直接折算，不读取时变粒子滤波或平均恒定建议模型。"
        />
        <CalculationBasisTimeline
          :start-time="dateTime(data.capacity_summary.cycle.starts_at)"
          :end-time="dateTime(data.capacity_summary.cycle.observed_at)"
          ><template #start-value
            ><CostBreakdownValue
              :total="data.capacity_summary.cycle.start_cost_usd"
              :breakdown="data.capacity_summary.cycle.start_cost_breakdown"
              :show-corrections="data.account.provider !== 'cpa'"
            />
            /
            {{
              formatPercent(data.capacity_summary.cycle.start_percent)
            }}</template
          ><template #end-value
            ><CostBreakdownValue
              :total="data.capacity_summary.cycle.end_cost_usd"
              :breakdown="data.capacity_summary.cycle.end_cost_breakdown"
              :show-corrections="data.account.provider !== 'cpa'"
            />
            /
            {{
              formatPercent(data.capacity_summary.cycle.end_percent)
            }}</template
          ></CalculationBasisTimeline
        >
        <div class="mt-3 rounded-box border border-base-300 p-4">
          <div class="text-center text-sm font-semibold opacity-60">
            周期累计端点公式
          </div>
          <p
            class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
          >
            ((<CostBreakdownValue
              :total="data.capacity_summary.cycle.end_cost_usd"
              :breakdown="data.capacity_summary.cycle.end_cost_breakdown"
              :show-corrections="data.account.provider !== 'cpa'"
              terms-only
            />) − (<CostBreakdownValue
              :total="data.capacity_summary.cycle.start_cost_usd"
              :breakdown="data.capacity_summary.cycle.start_cost_breakdown"
              :show-corrections="data.account.provider !== 'cpa'"
              terms-only
            />)) ÷ ({{
              formatPercent(data.capacity_summary.cycle.end_percent)
            }}
            − {{ formatPercent(data.capacity_summary.cycle.start_percent) }}) ×
            100 =
            {{ formatCurrency(data.capacity_summary.cycle.raw_estimate_usd) }}
          </p>
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
          :end-time="dateTime(data.capacity_summary.today.observed_to)"
          ><template #start-value
            ><CostBreakdownValue
              :total="data.capacity_summary.today.start_cost_usd"
              :breakdown="data.capacity_summary.today.start_cost_breakdown"
              :show-corrections="data.account.provider !== 'cpa'"
            />
            /
            {{
              formatPercent(data.capacity_summary.today.start_percent)
            }}</template
          ><template #end-value
            ><CostBreakdownValue
              :total="data.capacity_summary.today.end_cost_usd"
              :breakdown="data.capacity_summary.today.end_cost_breakdown"
              :show-corrections="data.account.provider !== 'cpa'"
            />
            /
            {{
              formatPercent(data.capacity_summary.today.end_percent)
            }}</template
          ></CalculationBasisTimeline
        >
        <div class="mt-3 rounded-box border border-base-300 p-4">
          <div class="text-center text-sm font-semibold opacity-60">
            日内增量公式
          </div>
          <p
            class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
          >
            ((<CostBreakdownValue
              :total="data.capacity_summary.today.end_cost_usd"
              :breakdown="data.capacity_summary.today.end_cost_breakdown"
              :show-corrections="data.account.provider !== 'cpa'"
              terms-only
            />) − (<CostBreakdownValue
              :total="data.capacity_summary.today.start_cost_usd"
              :breakdown="data.capacity_summary.today.start_cost_breakdown"
              :show-corrections="data.account.provider !== 'cpa'"
              terms-only
            />)) ÷ ({{
              formatPercent(data.capacity_summary.today.end_percent)
            }}
            − {{ formatPercent(data.capacity_summary.today.start_percent) }}) ×
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
