<script setup lang="ts">
import { onMounted, ref, watch } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api } from "@/services/api";
import type { MonitoredAccount } from "@/types/accounts";
import type { CapacityPoint, StatisticsData } from "@/types/statistics";

import CapacityBasisDialog from "./components/CapacityBasisDialog.vue";
import APIUsageBreakdownDialog from "./components/APIUsageBreakdownDialog.vue";
import CapacityOverviewCard from "./components/CapacityOverviewCard.vue";
import DailyClosingBasisDialog from "./components/DailyClosingBasisDialog.vue";
import ParticipantUsageCard from "./components/ParticipantUsageCard.vue";

interface BasisDialogHandle {
  open: (kind: "cycle" | "today") => void;
}

interface ClosingBasisDialogHandle {
  open: (point: CapacityPoint, kind: "cycle" | "daily") => void;
}

interface APIUsageDialogHandle {
  open: (
    participantId: number,
    participantName: string,
    accountId: number,
  ) => void;
}

const data = ref<StatisticsData | null>(null);
const accounts = ref<MonitoredAccount[]>([]);
const selectedAccountId = ref<number | null>(null);
const loading = ref(true);
const message = ref("");
const capacityPeriod = ref<"day" | "month">("day");
const capacityDays = ref(90);
const usageDays = ref(7);
const usagePrecision = ref<"raw" | "hour" | "day">("hour");
const basisDialog = ref<BasisDialogHandle | null>(null);
const closingBasisDialog = ref<ClosingBasisDialogHandle | null>(null);
const apiUsageDialog = ref<APIUsageDialogHandle | null>(null);

async function load() {
  loading.value = true;
  message.value = "";
  const query = new URLSearchParams({
    capacity_period: capacityPeriod.value,
    capacity_days: String(capacityDays.value),
    usage_days: String(usageDays.value),
    usage_precision: usagePrecision.value,
  });
  if (selectedAccountId.value != null) {
    query.set("account_id", String(selectedAccountId.value));
  }
  try {
    data.value = await api<StatisticsData>(`statistics?${query}`);
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "加载统计失败";
  } finally {
    loading.value = false;
  }
}

function showClosingBasis(point: CapacityPoint, kind: "cycle" | "daily") {
  closingBasisDialog.value?.open(point, kind);
}

function showApiUsage(participantId: number, participantName: string) {
  if (selectedAccountId.value == null) return;
  apiUsageDialog.value?.open(
    participantId,
    participantName,
    selectedAccountId.value,
  );
}

async function initialize() {
  try {
    accounts.value = await api<MonitoredAccount[]>(
      "settings/monitored-accounts",
    );
    selectedAccountId.value =
      accounts.value.find((item) => item.enabled)?.id ??
      accounts.value[0]?.id ??
      null;
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "加载账号失败";
  }
  await load();
}

watch(
  [selectedAccountId, capacityPeriod, capacityDays, usageDays, usagePrecision],
  load,
);
onMounted(initialize);
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
    <select
      v-if="accounts.length"
      v-model.number="selectedAccountId"
      class="select select-sm"
      :disabled="loading"
      aria-label="选择监控账号"
    >
      <option v-for="account in accounts" :key="account.id" :value="account.id">
        {{ account.name }}
      </option>
    </select>
    <button class="btn btn-sm" :disabled="loading" @click="load">
      <AppIcon name="arrow-path" class="size-4" />刷新
    </button>
  </PageShellHeader>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>

  <CapacityOverviewCard
    v-model:period="capacityPeriod"
    v-model:days="capacityDays"
    :data="data"
    :loading="loading"
    @show-basis="basisDialog?.open($event)"
    @show-closing-basis="showClosingBasis"
  />
  <ParticipantUsageCard
    v-model:days="usageDays"
    v-model:precision="usagePrecision"
    :data="data"
    :loading="loading"
    @show-api-usage="showApiUsage"
  />
  <CapacityBasisDialog v-if="data" ref="basisDialog" :data="data" />
  <DailyClosingBasisDialog
    ref="closingBasisDialog"
    :fast-correction-enabled="data?.fast_correction_enabled ?? false"
  />
  <APIUsageBreakdownDialog ref="apiUsageDialog" />
</template>
