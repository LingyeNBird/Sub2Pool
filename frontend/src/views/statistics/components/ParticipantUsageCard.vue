<script setup lang="ts">
import type { StatisticsData } from "@/types";
import { formatCurrency } from "@/utils/formatters";

defineProps<{
  data: StatisticsData | null;
  loading: boolean;
}>();

const usageDays = defineModel<number>("days", { required: true });
const usagePrecision = defineModel<"raw" | "hour" | "day">("precision", {
  required: true,
});
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="card-title">
            <AppIcon name="chart-bar" class="size-5" />参与者账号用量
            <span
              class="responsive-help-tooltip tooltip tooltip-bottom"
              :data-tip="`展示 Sub2API 用量日志中按所选 OpenAI 上游账号和参与者聚合的本周期累计用量。后台当前每 ${data?.sample_interval_minutes ?? '—'} 分钟探测一次。`"
            >
              <button
                type="button"
                class="btn btn-circle cursor-help btn-ghost btn-xs"
                aria-label="查看参与者账号用量说明"
              >
                ?
              </button>
            </span>
          </h2>
        </div>
        <div class="flex flex-wrap gap-2">
          <fieldset class="fieldset">
            <label class="label">时间范围</label>
            <select v-model.number="usageDays" class="select select-sm">
              <option :value="7">最近 7 天</option>
              <option :value="14">最近 14 天</option>
              <option :value="28">最近 28 天</option>
            </select>
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">图表精度</label>
            <select v-model="usagePrecision" class="select select-sm">
              <option value="raw">每次探测</option>
              <option value="hour">每小时末值</option>
              <option value="day">每天末值</option>
            </select>
          </fieldset>
        </div>
      </div>

      <div v-if="loading" class="flex justify-center py-16">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div
        v-else-if="data?.participant_series.length"
        class="grid gap-4 xl:grid-cols-2"
      >
        <article
          v-for="series in data.participant_series"
          :key="series.participant_id"
          class="rounded-box border border-base-300 bg-base-100 p-5"
        >
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h3 class="font-semibold">{{ series.participant_name }}</h3>
              <p class="text-sm opacity-60">
                Sub2API 账号 {{ series.sub2api_user_id }}
              </p>
            </div>
            <div class="text-right text-sm">
              <div>
                账号本周期用量：{{
                  formatCurrency(series.points.at(-1)?.account_cycle_usage_usd)
                }}
              </div>
              <div class="opacity-60">
                当前用户余额：{{
                  formatCurrency(series.points.at(-1)?.balance_usd)
                }}
              </div>
            </div>
          </div>
          <tc-line
            v-if="series.points.length"
            class="mt-4 block h-48 w-full"
            :values="series.points.map((item) => item.account_cycle_usage_usd)"
            :labels="series.points.map((item) => item.label)"
            :min="0"
            tooltip="@L · $@V"
          ></tc-line>
          <div v-else class="py-12 text-center text-sm opacity-60">
            尚无用量探测记录
          </div>
        </article>
      </div>
      <div v-else class="py-16 text-center opacity-60">尚未添加参与者。</div>

      <div class="alert text-sm alert-info">
        <AppIcon name="information-circle" class="size-5" />
        <span>
          “图表精度”只改变展示聚合方式；实际采集频率由系统设置中的“本地探测间隔”控制。
        </span>
      </div>
    </div>
  </section>
</template>
