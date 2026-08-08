<script setup lang="ts">
import { computed, ref } from "vue";

import CalculationBasisHeader from "@/components/common/CalculationBasisHeader.vue";
import CalculationBasisTimeline from "@/components/common/CalculationBasisTimeline.vue";
import { useDateTime } from "@/composables/useDateTime";
import type { CapacityPoint } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

type BasisKind = "cycle" | "daily";

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

const rateSourceDescription = computed(() => {
  if (!cycleBasis.value) return "";
  return (
    {
      current_interval_samples: `取收盘时最近 ${cycleBasis.value.rate_sample_count} 个有效累计样本，按已用百分比加权后采用 ${cycleBasis.value.conservative_percentile}% 保守分位。`,
      previous_interval_history:
        "该周期在收盘时还没有有效样本，因此沿用上一个正常周期的有效估值。",
      initial_fallback:
        "收盘时没有当前或历史有效样本，因此采用设置中的无样本默认值。",
    }[cycleBasis.value.rate_source] ||
    cycleBasis.value.sample_note ||
    "采用该收盘观测保存的美元/1%估值。"
  );
});

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
          help="展示该日最后一次有效观测当时保存的周期累计成本、官方百分比和保守美元/1%样本，而不是使用今天的最新数据倒推。"
        />
        <CalculationBasisTimeline
          :start-time="
            cycleBasis.starts_at ? dateTime(cycleBasis.starts_at) : '—'
          "
          :start-value="`${formatCurrency(cycleBasis.start_cost_usd)} / ${formatPercent(cycleBasis.start_percent)}`"
          :end-time="dateTime(cycleBasis.observed_at)"
          :end-value="`${formatCurrency(cycleBasis.end_cost_usd)} / ${formatPercent(cycleBasis.end_percent)}`"
        />

        <div class="mt-3 rounded-box border border-base-300 p-4">
          <div class="text-center text-sm font-semibold opacity-60">
            周期累计公式
          </div>
          <p
            v-if="cycleBasis.raw_estimate_usd !== null"
            class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
          >
            ({{ formatCurrency(cycleBasis.end_cost_usd) }} −
            {{ formatCurrency(cycleBasis.start_cost_usd) }}) ÷ ({{
              formatPercent(cycleBasis.end_percent)
            }}
            − {{ formatPercent(cycleBasis.start_percent) }}) × 100 =
            {{ formatCurrency(cycleBasis.raw_estimate_usd) }}
          </p>
          <p v-else class="mt-2 text-center text-sm opacity-70">
            收盘时官方已用百分比为 0，不能形成累计端点折算。
          </p>
          <p class="mt-3 text-sm opacity-70">
            {{ rateSourceDescription }} 当时采用
            <strong>{{
              formatCurrency(cycleBasis.effective_usd_per_percent)
            }}</strong>
            / 1%，所以该日收盘估算为
            <strong>{{ formatCurrency(cycleBasis.estimate_usd) }}</strong
            >。
          </p>
        </div>

        <div v-if="cycleBasis.rate_samples.length" class="mt-3 overflow-x-auto">
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
                v-for="sample in cycleBasis.rate_samples"
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

      <template v-else-if="dailyBasis">
        <CalculationBasisHeader
          :title="`${point.period} 日内折算依据`"
          help="只比较该日同一归属区间内最早和最晚的有效观测，不从周期起点累计；未达到设置的最小周限跨度时不会形成历史点。"
        />
        <CalculationBasisTimeline
          :start-time="dateTime(dailyBasis.observed_from)"
          :start-value="`${formatCurrency(dailyBasis.start_cost_usd)} / ${formatPercent(dailyBasis.start_percent)}`"
          :end-time="dateTime(dailyBasis.observed_to)"
          :end-value="`${formatCurrency(dailyBasis.end_cost_usd)} / ${formatPercent(dailyBasis.end_percent)}`"
        />

        <div class="mt-3 rounded-box border border-base-300 p-4">
          <div class="text-center text-sm font-semibold opacity-60">
            日内增量公式
          </div>
          <p
            class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
          >
            ({{ formatCurrency(dailyBasis.end_cost_usd) }} −
            {{ formatCurrency(dailyBasis.start_cost_usd) }}) ÷ ({{
              formatPercent(dailyBasis.end_percent)
            }}
            − {{ formatPercent(dailyBasis.start_percent) }}) × 100 =
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
