<script setup lang="ts">
import { computed, ref } from "vue";

import CalculationBasisHeader from "@/components/common/CalculationBasisHeader.vue";
import CalculationBasisTimeline from "@/components/common/CalculationBasisTimeline.vue";
import CostBreakdownValue from "@/components/common/CostBreakdownValue.vue";
import { useDateTime } from "@/composables/useDateTime";
import type { CapacityPoint } from "@/types/statistics";
import { formatCurrency, formatPercent } from "@/utils/formatters";

type BasisKind = "cycle" | "daily";
withDefaults(defineProps<{ showCorrections?: boolean }>(), {
  showCorrections: true,
});

const dialog = ref<HTMLDialogElement | null>(null);
const point = ref<CapacityPoint | null>(null);
const basisKind = ref<BasisKind>("cycle");
const cycleBasis = computed(() =>
  basisKind.value === "cycle" ? (point.value?.basis ?? null) : null,
);
const dailyBasis = computed(() =>
  basisKind.value === "daily" ? (point.value?.daily_basis ?? null) : null,
);
const dateTime = useDateTime();

function open(selected: CapacityPoint, kind: BasisKind) {
  const selectedBasis =
    kind === "cycle" ? selected.basis : selected.daily_basis;
  if (!selectedBasis) return;
  point.value = selected;
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
    <div v-if="point" class="modal-box max-w-3xl">
      <form method="dialog">
        <button
          class="btn absolute top-3 right-3 btn-circle btn-ghost btn-sm"
          aria-label="关闭"
        >
          ✕
        </button>
      </form>

      <template v-if="cycleBasis">
        <CalculationBasisHeader
          :title="`${point.period} 累计收盘依据`"
          help="展示该日最后一次观测在当时可获得的周期累计端点折算；不使用时变归属模型，也不使用今天的最新数据回填历史。"
        />
        <CalculationBasisTimeline
          :start-time="
            cycleBasis.starts_at ? dateTime(cycleBasis.starts_at) : '—'
          "
          :end-time="dateTime(cycleBasis.observed_at)"
          ><template #start-value
            ><CostBreakdownValue
              :total="cycleBasis.start_cost_usd"
              :breakdown="cycleBasis.start_cost_breakdown"
              :show-corrections="showCorrections"
            />
            / {{ formatPercent(cycleBasis.start_percent) }}</template
          ><template #end-value
            ><CostBreakdownValue
              :total="cycleBasis.end_cost_usd"
              :breakdown="cycleBasis.end_cost_breakdown"
              :show-corrections="showCorrections"
            />
            / {{ formatPercent(cycleBasis.end_percent) }}</template
          ></CalculationBasisTimeline
        >

        <div class="mt-3 rounded-box border border-base-300 p-4">
          <div class="text-center text-sm font-semibold opacity-60">
            周期累计公式
          </div>
          <p
            v-if="cycleBasis.raw_estimate_usd !== null"
            class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
          >
            ((<CostBreakdownValue
              :total="cycleBasis.end_cost_usd"
              :breakdown="cycleBasis.end_cost_breakdown"
              :show-corrections="showCorrections"
              terms-only
            />) − (<CostBreakdownValue
              :total="cycleBasis.start_cost_usd"
              :breakdown="cycleBasis.start_cost_breakdown"
              :show-corrections="showCorrections"
              terms-only
            />)) ÷ ({{ formatPercent(cycleBasis.end_percent) }} −
            {{ formatPercent(cycleBasis.start_percent) }}) × 100 =
            {{ formatCurrency(cycleBasis.raw_estimate_usd) }}
          </p>
          <p v-else class="mt-2 text-center text-sm opacity-70">
            收盘时官方已用百分比为 0，不能形成累计端点折算。
          </p>
          <p class="mt-3 text-sm opacity-70">
            该日收盘值只由上面的累计成本与累计整数百分比确定。
          </p>
        </div>
      </template>

      <template v-else-if="dailyBasis">
        <CalculationBasisHeader
          :title="`${point.period} 日内折算依据`"
          help="只比较该日同一归属区间内最早和最晚的有效观测，不从周期起点累计；未达到设置的最小周限跨度时不会形成历史点。"
        />
        <CalculationBasisTimeline
          :start-time="dateTime(dailyBasis.observed_from)"
          :end-time="dateTime(dailyBasis.observed_to)"
          ><template #start-value
            ><CostBreakdownValue
              :total="dailyBasis.start_cost_usd"
              :breakdown="dailyBasis.start_cost_breakdown"
              :show-corrections="showCorrections"
            />
            / {{ formatPercent(dailyBasis.start_percent) }}</template
          ><template #end-value
            ><CostBreakdownValue
              :total="dailyBasis.end_cost_usd"
              :breakdown="dailyBasis.end_cost_breakdown"
              :show-corrections="showCorrections"
            />
            / {{ formatPercent(dailyBasis.end_percent) }}</template
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
              :total="dailyBasis.end_cost_usd"
              :breakdown="dailyBasis.end_cost_breakdown"
              :show-corrections="showCorrections"
              terms-only
            />) − (<CostBreakdownValue
              :total="dailyBasis.start_cost_usd"
              :breakdown="dailyBasis.start_cost_breakdown"
              :show-corrections="showCorrections"
              terms-only
            />)) ÷ ({{ formatPercent(dailyBasis.end_percent) }} −
            {{ formatPercent(dailyBasis.start_percent) }}) × 100 =
            {{ formatCurrency(dailyBasis.estimate_usd) }}
          </p>
          <p class="mt-3 text-sm opacity-70">
            共使用 {{ dailyBasis.sample_count }} 次观测，实际覆盖
            {{ formatPercent(dailyBasis.percent_delta) }}，要求至少覆盖
            {{
              formatPercent(dailyBasis.min_percent_span)
            }}。考虑整数百分比端点误差，估算范围约为
            <strong>{{ formatCurrency(dailyBasis.minimum_usd) }}</strong>
            至
            <strong>{{ formatCurrency(dailyBasis.maximum_usd) }}</strong
            >。
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
