<script setup lang="ts">
import { computed, ref } from "vue";

import CalculationBasisHeader from "@/components/common/CalculationBasisHeader.vue";
import CalculationBasisTimeline from "@/components/common/CalculationBasisTimeline.vue";
import { useDateTime } from "@/composables/useDateTime";
import type { CapacityPoint } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

const dialog = ref<HTMLDialogElement | null>(null);
const point = ref<CapacityPoint | null>(null);
const basis = computed(() => point.value?.basis ?? null);
const dateTime = useDateTime();

const rateSourceDescription = computed(() => {
  if (!basis.value) return "";
  return (
    {
      current_interval_samples: `取收盘时最近 ${basis.value.rate_sample_count} 个有效累计样本，按已用百分比加权后采用 ${basis.value.conservative_percentile}% 保守分位。`,
      previous_interval_history:
        "该周期在收盘时还没有有效样本，因此沿用上一个正常周期的有效估值。",
      initial_fallback:
        "收盘时没有当前或历史有效样本，因此采用设置中的无样本默认值。",
    }[basis.value.rate_source] ||
    basis.value.sample_note ||
    "采用该收盘观测保存的美元/1%估值。"
  );
});

function open(selected: CapacityPoint) {
  if (!selected.basis) return;
  point.value = selected;
  dialog.value?.showModal();
}

function close() {
  dialog.value?.close();
}

defineExpose({ open, close });
</script>

<template>
  <dialog ref="dialog" class="modal">
    <div v-if="point && basis" class="modal-box max-w-3xl">
      <form method="dialog">
        <button
          class="btn absolute top-3 right-3 btn-circle btn-ghost btn-sm"
          aria-label="关闭"
        >
          ✕
        </button>
      </form>

      <CalculationBasisHeader
        :title="`${point.period} 收盘估算依据`"
        help="展示该日最后一次有效观测当时保存的累计成本、官方百分比和保守美元/1%样本，而不是使用今天的最新数据倒推。"
      />
      <CalculationBasisTimeline
        :start-time="basis.starts_at ? dateTime(basis.starts_at) : '—'"
        :start-value="`${formatCurrency(basis.start_cost_usd)} / ${formatPercent(basis.start_percent)}`"
        :end-time="dateTime(basis.observed_at)"
        :end-value="`${formatCurrency(basis.end_cost_usd)} / ${formatPercent(basis.end_percent)}`"
      />

      <div class="mt-3 rounded-box border border-base-300 p-4">
        <div class="text-center text-sm font-semibold opacity-60">
          收盘累计公式
        </div>
        <p
          v-if="basis.raw_estimate_usd !== null"
          class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
        >
          ({{ formatCurrency(basis.end_cost_usd) }} −
          {{ formatCurrency(basis.start_cost_usd) }}) ÷ ({{
            formatPercent(basis.end_percent)
          }}
          − {{ formatPercent(basis.start_percent) }}) × 100 =
          {{ formatCurrency(basis.raw_estimate_usd) }}
        </p>
        <p v-else class="mt-2 text-center text-sm opacity-70">
          收盘时官方已用百分比为 0，不能形成累计端点折算。
        </p>
        <p class="mt-3 text-sm opacity-70">
          {{ rateSourceDescription }} 当时采用
          <strong>{{ formatCurrency(basis.effective_usd_per_percent) }}</strong>
          / 1%，所以该日收盘估算为
          <strong>{{ formatCurrency(basis.estimate_usd) }}</strong
          >。
        </p>
      </div>

      <div v-if="basis.rate_samples.length" class="mt-3 overflow-x-auto">
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
            <tr v-for="sample in basis.rate_samples" :key="sample.observed_at">
              <td>{{ dateTime(sample.observed_at) }}</td>
              <td>{{ formatCurrency(sample.cost_usd) }}</td>
              <td>{{ formatPercent(sample.used_percent) }}</td>
              <td>{{ formatCurrency(sample.usd_per_percent) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="modal-action">
        <button class="btn" @click="close">关闭</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
  </dialog>
</template>
