<script setup lang="ts">
import { onMounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api } from "@/services/api";
import type { DashboardData, Participant } from "@/types";

import AccountExplanationCard from "./components/AccountExplanationCard.vue";
import CollectionStatusCard from "./components/CollectionStatusCard.vue";
import CycleRateBasisDialog from "./components/CycleRateBasisDialog.vue";
import DashboardStats from "./components/DashboardStats.vue";
import RecommendationActionDialog from "./components/RecommendationActionDialog.vue";
import RecommendationList from "./components/RecommendationList.vue";

interface DialogHandle {
  open: (...args: never[]) => void;
  close: () => void;
}

interface RecommendationDialogHandle {
  open: (participant: Participant) => void;
  close: () => void;
}

const data = ref<DashboardData | null>(null);
const selectedAccountId = ref<number | null>(null);
const loading = ref(true);
const running = ref(false);
const message = ref("");
const applyingParticipantId = ref<number | null>(null);
const appliedParticipantIds = ref<number[]>([]);
const actionToast = ref("");
const rateBasisDialog = ref<DialogHandle | null>(null);
const actionDialog = ref<RecommendationDialogHandle | null>(null);
const demoMode = import.meta.env.VITE_DEMO_MODE === "true";

async function load() {
  loading.value = true;
  message.value = "";
  try {
    const query =
      selectedAccountId.value == null
        ? ""
        : `?account_id=${selectedAccountId.value}`;
    data.value = await api<DashboardData>(`dashboard${query}`);
    selectedAccountId.value = data.value.selected_account_id;
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "加载总览失败";
  } finally {
    loading.value = false;
  }
}

async function runCalibration() {
  running.value = true;
  message.value = "";
  try {
    await api("monitor/run", {
      method: "POST",
      body: JSON.stringify({ account_id: selectedAccountId.value }),
    });
    await load();
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "测算失败";
  } finally {
    running.value = false;
  }
}

function openAdminApi() {
  if (demoMode) {
    actionToast.value = "公开演示不会连接真实 Sub2API 管理端";
    window.setTimeout(() => {
      actionToast.value = "";
    }, 2600);
    return;
  }
  if (data.value?.sub2api_admin_url) {
    window.open(data.value.sub2api_admin_url, "_blank", "noopener,noreferrer");
  }
}

async function applyRecommendation(participant: Participant) {
  if (
    participant.snapshot?.recommended_balance_usd == null ||
    participant.snapshot.recommended_balance_usd <= 0 ||
    applyingParticipantId.value != null
  ) {
    return;
  }

  applyingParticipantId.value = participant.id;
  actionToast.value = "";
  message.value = "";
  try {
    await api(`dashboard/participants/${participant.id}/apply-recommendation`, {
      method: "POST",
    });
    appliedParticipantIds.value = [
      ...appliedParticipantIds.value,
      participant.id,
    ];
    actionDialog.value?.close();
  } catch (error) {
    actionToast.value = "一键设置失败";
    message.value =
      error instanceof ApiError ? error.message : "一键设置额度失败";
  } finally {
    applyingParticipantId.value = null;
  }
}

onMounted(load);
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">额度管理</RouterLink></li>
          <li><h1>额度总览</h1></li>
        </ul>
      </div>
    </div>
    <div class="flex flex-wrap gap-2">
      <select
        v-if="data?.accounts.length"
        v-model.number="selectedAccountId"
        class="select select-sm"
        :disabled="loading || running"
        aria-label="选择监控账号"
        @change="load"
      >
        <option
          v-for="account in data.accounts"
          :key="account.id"
          :value="account.id"
        >
          {{ account.name }}
        </option>
      </select>
      <button
        class="btn btn-primary btn-sm"
        :disabled="running"
        @click="runCalibration"
      >
        <span v-if="running" class="loading loading-xs loading-spinner"></span>
        <AppIcon v-else name="arrow-path" class="size-4" />
        {{ running ? "测算中" : "立即测算" }}
      </button>
      <RouterLink to="/settings" class="btn btn-sm">
        <AppIcon name="cog-6-tooth" class="size-4" />
        系统设置
      </RouterLink>
    </div>
  </PageShellHeader>

  <div v-if="actionToast" class="toast toast-center toast-top z-50">
    <div class="alert alert-error shadow-lg">
      <AppIcon name="exclamation-triangle" class="size-5" />
      <span>{{ actionToast }}</span>
    </div>
  </div>
  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>
  <div
    v-if="data?.quota_query_mode === 'passive'"
    class="col-span-12 alert alert-info"
  >
    <AppIcon name="information-circle" class="size-5" />
    <span>
      当前为被动查询：只读取 Sub2API 已保存的账号快照，不会向 OpenAI
      官方额度接口发起请求。
    </span>
  </div>
  <div v-if="data && !data.configured" class="col-span-12 alert alert-warning">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>
      尚未完成 Sub2API 连接配置。请先在系统设置中填写地址、Admin Token
      并添加至少一个 OpenAI 监控账号。
    </span>
  </div>

  <DashboardStats
    v-if="data"
    :data="data"
    @show-rate-basis="rateBasisDialog?.open()"
  />
  <section v-if="loading" class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body items-center">
      <span class="loading loading-lg loading-spinner"></span>
    </div>
  </section>
  <RecommendationList
    v-if="data"
    :participants="data.participants"
    :applied-participant-ids="appliedParticipantIds"
    @select="actionDialog?.open($event)"
  />
  <CollectionStatusCard v-if="data" :data="data" />
  <AccountExplanationCard v-if="data" :data="data" />

  <RecommendationActionDialog
    v-if="data"
    ref="actionDialog"
    :admin-url="data.sub2api_admin_url"
    :applying-participant-id="applyingParticipantId"
    @open-admin="openAdminApi"
    @apply="applyRecommendation"
  />
  <CycleRateBasisDialog v-if="data" ref="rateBasisDialog" :data="data" />
</template>
