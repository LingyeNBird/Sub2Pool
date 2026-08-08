<script setup lang="ts">
import { onMounted, ref, watch } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api } from "@/services/api";
import type { CapacityPoint, StatisticsData } from "@/types";

import CapacityBasisDialog from "./components/CapacityBasisDialog.vue";
import CapacityOverviewCard from "./components/CapacityOverviewCard.vue";
import DailyClosingBasisDialog from "./components/DailyClosingBasisDialog.vue";
import ParticipantUsageCard from "./components/ParticipantUsageCard.vue";

interface BasisDialogHandle {
  open: (kind: "cycle" | "today") => void;
}

interface ClosingBasisDialogHandle {
  open: (point: CapacityPoint, kind: "cycle" | "daily") => void;
}

const data = ref<StatisticsData | null>(null);
const loading = ref(true);
const message = ref("");
const capacityPeriod = ref<"day" | "month">("day");
const capacityDays = ref(90);
const usageDays = ref(7);
const usagePrecision = ref<"raw" | "hour" | "day">("hour");
const basisDialog = ref<BasisDialogHandle | null>(null);
const closingBasisDialog = ref<ClosingBasisDialogHandle | null>(null);

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

function showClosingBasis(point: CapacityPoint, kind: "cycle" | "daily") {
  closingBasisDialog.value?.open(point, kind);
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
  />
  <CapacityBasisDialog v-if="data" ref="basisDialog" :data="data" />
  <DailyClosingBasisDialog ref="closingBasisDialog" />
</template>
