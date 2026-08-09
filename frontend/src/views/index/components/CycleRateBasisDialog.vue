<script setup lang="ts">
import { ref } from "vue";

import CalculationBasisHeader from "@/components/common/CalculationBasisHeader.vue";
import CalculationBasisTimeline from "@/components/common/CalculationBasisTimeline.vue";
import { useDateTime } from "@/composables/useDateTime";
import type { DashboardData } from "@/types";
import {
  formatCostBreakdown,
  formatCostTerms,
  formatCurrency,
  formatPercent,
} from "@/utils/formatters";

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
          :title="
            data.weekly_quota_model === 'constant_average'
              ? '平均美元 / 1% 计算依据'
              : '保守美元 / 1% 计算依据'
          "
          :help="
            data.weekly_quota_model === 'constant_average'
              ? '平均恒定模式直接使用周期起点至当前观测的累计成本和已用百分比。'
              : '每个有效样本都从本周期 0 美元、0% 起算；系统按已用百分比加权后取保守分位。'
          "
        />
        <CalculationBasisTimeline
          v-if="data.weekly_quota_model === 'constant_average'"
          :start-time="dateTime(data.cycle.starts_at)"
          :start-value="`${formatCostBreakdown(
            0,
            data.cycle.start_cost_breakdown,
            data.fast_correction_enabled,
          )} / ${formatPercent(0)}`"
          end-label="当前累计终点"
          :end-time="dateTime(data.cycle.observed_at)"
          :end-value="`${formatCostBreakdown(
            data.cycle.selected_total_cost,
            data.cycle.selected_total_cost_breakdown,
            data.fast_correction_enabled,
          )} / ${formatPercent(data.cycle.interval_used_percent)}`"
        />
        <CalculationBasisTimeline
          v-else-if="data.cycle.rate_samples[0]"
          :start-time="dateTime(data.cycle.starts_at)"
          :start-value="`${formatCostBreakdown(
            0,
            data.cycle.start_cost_breakdown,
            data.fast_correction_enabled,
          )} / ${formatPercent(0)}`"
          end-label="最近有效样本终点"
          :end-time="dateTime(data.cycle.rate_samples[0].observed_at)"
          :end-value="`${formatCostBreakdown(
            data.cycle.rate_samples[0].cost_usd,
            data.cycle.rate_samples[0].cost_breakdown,
            data.fast_correction_enabled,
          )} / ${formatPercent(data.cycle.rate_samples[0].used_percent)}`"
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
          <p class="mt-2 text-sm opacity-70">
            平均恒定模式直接采用上述起点至终点的累计折算，不使用历史样本保守分位。
          </p>
        </div>
        <div
          v-else-if="data.cycle.rate_samples[0]"
          class="mt-3 rounded-box border border-base-300 p-4"
        >
          <div class="text-center text-sm font-semibold opacity-60">
            最近样本公式
          </div>
          <p
            class="mt-2 text-center font-mono text-base leading-relaxed font-semibold sm:text-lg"
          >
            ({{
              formatCostTerms(
                data.cycle.rate_samples[0].cost_usd,
                data.cycle.rate_samples[0].cost_breakdown,
                data.fast_correction_enabled,
              )
            }}) ÷ {{ formatPercent(data.cycle.rate_samples[0].used_percent) }} =
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
        <div
          v-if="data.weekly_quota_model === 'time_varying'"
          class="mt-3 overflow-x-auto"
        >
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
                <td>
                  {{
                    formatCostBreakdown(
                      sample.cost_usd,
                      sample.cost_breakdown,
                      data.fast_correction_enabled,
                    )
                  }}
                </td>
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
