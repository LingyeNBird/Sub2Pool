<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api } from "@/services/api";
import type { StatisticsData } from "@/types";

const data = ref<StatisticsData | null>(null);
const loading = ref(true);
const message = ref("");
const capacityPeriod = ref<"day" | "month">("day");
const capacityDays = ref(90);
const usageDays = ref(7);
const usagePrecision = ref<"raw" | "hour" | "day">("hour");

const capacityValues = computed(
  () => data.value?.capacity_series.map((item) => item.weekly_total_usd) ?? [],
);
const capacityLabels = computed(
  () => data.value?.capacity_series.map((item) => item.period) ?? [],
);
const latestCapacity = computed(
  () => data.value?.capacity_series.at(-1) ?? null,
);

function currency(value: number | null | undefined) {
  return value == null ? "—" : `$${value.toFixed(2)}`;
}

async function load() {
  loading.value = true;
  message.value = "";
  const query = new URLSearchParams({
    capacity_period: capacityPeriod.value,
    capacity_days: String(capacityDays.value),
    usage_days: String(usageDays.value),
    usage_precision: usagePrecision.value,
  });
  try {
    data.value = await api<StatisticsData>(`statistics?${query}`);
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "加载统计失败";
  } finally {
    loading.value = false;
  }
}

watch([capacityPeriod, capacityDays, usageDays, usagePrecision], load);
onMounted(load);
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">额度管理</RouterLink></li>
          <li><h1>额度统计</h1></li>
        </ul>
      </div>
    </div>
    <button class="btn btn-sm" :disabled="loading" @click="load">
      <AppIcon name="arrow-path" class="size-4" />刷新
    </button>
  </PageShellHeader>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>

  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-5">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 class="card-title">
            <AppIcon
              name="presentation-chart-line"
              class="size-5"
            />周限总额度估算
          </h2>
          <p class="mt-1 text-sm opacity-60">
            日视图取当天最后一次保守估算；月视图取每天收盘估算的平均值。
          </p>
        </div>
        <div class="flex flex-wrap gap-2">
          <fieldset class="fieldset">
            <label class="label">统计维度</label>
            <select v-model="capacityPeriod" class="select select-sm">
              <option value="day">按天</option>
              <option value="month">按月</option>
            </select>
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">查看范围</label>
            <select v-model.number="capacityDays" class="select select-sm">
              <option :value="30">最近 30 天</option>
              <option :value="90">最近 90 天</option>
              <option :value="365">最近 1 年</option>
              <option :value="730">最近 2 年</option>
            </select>
          </fieldset>
        </div>
      </div>

      <div class="stats stats-vertical bg-base-100 lg:stats-horizontal">
        <div class="stat">
          <div class="stat-title">最近周限总额度</div>
          <div class="stat-value text-xl font-semibold">
            {{ currency(latestCapacity?.weekly_total_usd) }}
          </div>
        </div>
        <div class="stat">
          <div class="stat-title">区间最低</div>
          <div class="stat-value text-xl font-semibold">
            {{ currency(latestCapacity?.minimum_usd) }}
          </div>
        </div>
        <div class="stat">
          <div class="stat-title">区间最高</div>
          <div class="stat-value text-xl font-semibold">
            {{ currency(latestCapacity?.maximum_usd) }}
          </div>
        </div>
      </div>

      <div v-if="loading" class="flex justify-center py-16">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <tc-line
        v-else-if="capacityValues.length"
        class="block h-64 w-full"
        :values="capacityValues"
        :labels="capacityLabels"
        :min="0"
        tooltip="@L · $@V"
      ></tc-line>
      <div v-else class="py-16 text-center opacity-60">
        尚无校准观测，完成首次测算后才会形成周限总额度历史。
      </div>
    </div>
  </section>

  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-5">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 class="card-title">
            <AppIcon name="chart-bar" class="size-5" />参与者账号用量
          </h2>
          <p class="mt-1 text-sm opacity-60">
            展示 Sub2API 用量日志中按所选 OpenAI
            上游账号和参与者聚合的本周期累计用量。后台当前每
            {{ data?.sample_interval_minutes ?? "—" }} 分钟探测一次。
          </p>
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
                  currency(series.points.at(-1)?.account_cycle_usage_usd)
                }}
              </div>
              <div class="opacity-60">
                当前用户余额：{{ currency(series.points.at(-1)?.balance_usd) }}
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
