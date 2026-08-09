<script setup lang="ts">
import { computed, ref } from "vue";

import CalculationBasisHeader from "@/components/common/CalculationBasisHeader.vue";
import CalculationBasisTimeline from "@/components/common/CalculationBasisTimeline.vue";
import { useDateTime } from "@/composables/useDateTime";
import type { DashboardData, ModelDiagnostics } from "@/types";
import {
  formatCostBreakdown,
  formatCostTerms,
  formatCurrency,
  formatPercent,
} from "@/utils/formatters";

const props = defineProps<{
  data: DashboardData;
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const dateTime = useDateTime();
const diagnostics = computed<ModelDiagnostics | null>(() => {
  const value = props.data.cycle?.model_diagnostics;
  if (
    !value ||
    typeof (value as Partial<ModelDiagnostics>).algorithm !== "string"
  ) {
    return null;
  }
  return value as ModelDiagnostics;
});

const quantizerLabels: Record<string, string> = {
  floor: "向下取整",
  nearest: "四舍五入",
  ceil: "向上取整",
};

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
          :title="
            data.weekly_quota_model === 'constant_average'
              ? '平均美元 / 1% 计算依据'
              : '时变容量模型依据'
          "
          :help="
            data.weekly_quota_model === 'constant_average'
              ? '平均恒定模式直接使用周期起点至当前观测的累计成本和已用百分比。'
              : '时变模式同时估计连续容量路径、整数显示规则和各账号归属；确定性边界用于阻止概率估计越过硬约束。'
          "
        />
        <CalculationBasisTimeline
          :start-time="dateTime(data.cycle.starts_at)"
          :start-value="`${formatCostBreakdown(
            0,
            data.cycle.start_cost_breakdown,
            data.fast_correction_enabled,
          )} / ${formatPercent(0)}`"
          end-label="当前观测终点"
          :end-time="dateTime(data.cycle.observed_at)"
          :end-value="`${formatCostBreakdown(
            data.cycle.selected_total_cost,
            data.cycle.selected_total_cost_breakdown,
            data.fast_correction_enabled,
          )} / 显示 ${formatPercent(data.cycle.interval_used_percent)}`"
        />
        <div
          v-if="data.weekly_quota_model === 'constant_average'"
          class="mt-3 rounded-box border border-base-300 p-4"
        >
          <div class="text-center text-sm font-semibold opacity-60">
            周期累计公式
          </div>
          <p
            class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
          >
            ({{
              formatCostTerms(
                data.cycle.selected_total_cost,
                data.cycle.selected_total_cost_breakdown,
                data.fast_correction_enabled,
              )
            }}) ÷ {{ formatPercent(data.cycle.interval_used_percent) }} =
            {{ formatCurrency(data.cycle.effective_usd_per_percent) }} / 1%
          </p>
        </div>
        <div
          v-else-if="diagnostics"
          class="mt-3 space-y-4 rounded-box border border-base-300 p-4"
        >
          <div>
            <div class="text-center text-sm font-semibold opacity-60">
              当前容量结论
            </div>
            <p
              class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
            >
              {{
                formatCurrency(
                  data.cycle.effective_usd_per_percent === null
                    ? null
                    : data.cycle.effective_usd_per_percent * 100,
                )
              }}
              / 周期
            </p>
            <p class="text-center text-sm opacity-70">
              90% 概率区间
              {{ formatCurrency(data.cycle.capacity_lower_usd) }} –
              {{ formatCurrency(data.cycle.capacity_upper_usd) }}
            </p>
          </div>
          <div class="grid gap-3 sm:grid-cols-2">
            <div class="rounded-box bg-base-300/40 p-3">
              <div class="text-xs opacity-60">潜在真实进度</div>
              <div class="mt-1 font-semibold">
                {{ formatPercent(data.cycle.estimated_used_percent) }}
              </div>
              <div class="mt-1 text-xs opacity-60">
                概率区间
                {{
                  formatPercent(diagnostics.progress_probability_interval[0])
                }}
                –
                {{
                  formatPercent(diagnostics.progress_probability_interval[1])
                }}
              </div>
            </div>
            <div class="rounded-box bg-base-300/40 p-3">
              <div class="text-xs opacity-60">确定性进度边界</div>
              <div class="mt-1 font-semibold">
                {{
                  formatPercent(diagnostics.progress_deterministic_bounds[0])
                }}
                –
                {{
                  formatPercent(diagnostics.progress_deterministic_bounds[1])
                }}
              </div>
              <div class="mt-1 text-xs opacity-60">
                未映射成本
                {{ formatCurrency(diagnostics.residual_cost_usd) }}
              </div>
            </div>
          </div>
          <div class="overflow-x-auto">
            <table class="table table-sm">
              <thead>
                <tr>
                  <th>候选整数显示规则</th>
                  <th>当前概率</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="[name, probability] in Object.entries(
                    diagnostics.quantizer_probabilities,
                  )"
                  :key="name"
                >
                  <td>{{ quantizerLabels[name] ?? name }}</td>
                  <td>{{ formatPercent(probability * 100) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="overflow-x-auto">
            <table class="table table-sm">
              <thead>
                <tr>
                  <th>候选变化速度</th>
                  <th>当前概率</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="[name, probability] in Object.entries(
                    diagnostics.speed_probabilities,
                  )"
                  :key="name"
                >
                  <td>{{ name }}</td>
                  <td>{{ formatPercent(probability * 100) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="text-xs opacity-60">
            {{ diagnostics.particles }} 个粒子；有效样本比例
            {{ formatPercent(diagnostics.ess_fraction * 100) }}。{{
              diagnostics.prior_capacity_usd === null
                ? "首次历史区间使用宽初始分布。"
                : `以上周期 ${formatCurrency(diagnostics.prior_capacity_usd)} 为软先验。`
            }}
            相同原始事实和区间起点会使用相同种子，重放结果可复现。
          </p>
          <p
            v-if="Math.abs(diagnostics.aggregate_cost_difference_usd) >= 0.01"
            class="text-xs opacity-60"
          >
            总成本与用户成本合计差额：
            {{ formatCurrency(diagnostics.aggregate_cost_difference_usd) }}。
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
