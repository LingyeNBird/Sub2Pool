<script setup lang="ts">
import { computed, ref } from "vue";
import PageShellHeader from "@/components/common/PageShellHeader.vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import { useAuthStore } from "@/stores/auth";
import type { ConfirmDialogHandle, ConfirmDialogOptions } from "@/types/common";

import AllocationModelCard from "./components/AllocationModelCard.vue";
import DatabaseTransferCard from "./components/DatabaseTransferCard.vue";
import CPAConnectionCard from "./components/CPAConnectionCard.vue";
import DataMaintenanceCard from "./components/DataMaintenanceCard.vue";
import EmailServiceCard from "./components/EmailServiceCard.vue";
import BillingCorrectionCard from "./components/BillingCorrectionCard.vue";
import LoginSecurityCard from "./components/LoginSecurityCard.vue";
import ReadOnlyAPIKeyCard from "./components/ReadOnlyAPIKeyCard.vue";
import NotificationRulesCard from "./components/NotificationRulesCard.vue";
import SamplingStrategyCard from "./components/SamplingStrategyCard.vue";
import Sub2APIConnectionCard from "./components/Sub2APIConnectionCard.vue";
import { useSettingsPage } from "./composables/useSettingsPage";

const auth = useAuthStore();
const confirmDialog = ref<ConfirmDialogHandle | null>(null);
const demoMode = import.meta.env.VITE_DEMO_MODE === "true";

function confirmAction(options: ConfirmDialogOptions) {
  return confirmDialog.value?.open(options) ?? Promise.resolve(false);
}

const {
  settings,
  personalApiKey,
  loading,
  saving,
  testing,
  message,
  success,
  adminToken,
  cpaManagementKey,
  smtpPassword,
  resendApiKey,
  openAIAccounts,
  cpaAccounts,
  monitoredAccounts,
  selectedTestAccountId,
  maintenanceAccountId,
  savingAccountId,
  loadingAccounts,
  loadingCPAAccounts,
  exportingDatabase,
  importingDatabase,
  historyRebuildPlan,
  generatingReadOnlyApiKey,
  revokingReadOnlyApiKey,
  planningHistory,
  applyingHistory,
  passwordForm,
  loadOpenAIAccounts,
  loadCPAAccounts,
  saveMonitoredAccount,
  saveConnection,
  saveCPASettings,
  saveCPAPricing,
  saveAllocation,
  saveSampling,
  saveEmail,
  saveBillingCorrection,
  saveNotifications,
  exportDatabase,
  importDatabase,
  createHistoricalRebuildPlan,
  applyHistoricalRebuildPlan,
  test,
  generateReadOnlyApiKey,
  revokeReadOnlyApiKey,
  changePassword,
} = useSettingsPage(confirmAction);

const readOnlyAPIKeyCard = ref<InstanceType<typeof ReadOnlyAPIKeyCard> | null>(
  null,
);

const apiKeyConfigured = computed(() =>
  auth.isStaff
    ? (settings.value?.readonly_api_key_configured ?? false)
    : (personalApiKey.value?.configured ?? false),
);
const apiKeyHint = computed(() =>
  auth.isStaff
    ? (settings.value?.readonly_api_key_hint ?? "")
    : (personalApiKey.value?.hint ?? ""),
);
const apiKeyCreatedAt = computed(() =>
  auth.isStaff
    ? (settings.value?.readonly_api_key_created_at ?? null)
    : (personalApiKey.value?.created_at ?? null),
);

const sub2apiMonitoredAccounts = computed(() =>
  monitoredAccounts.value.filter((account) => account.provider === "sub2api"),
);
const cpaMonitoredAccounts = computed(() =>
  monitoredAccounts.value.filter((account) => account.provider === "cpa"),
);

async function handleGenerateReadOnlyAPIKey() {
  if (
    apiKeyConfigured.value &&
    !(await confirmAction({
      title: "重新生成 API Key？",
      message:
        "重新生成后，原 API Key 会立即失效，所有调用方都必须改用新 Key。",
      confirmLabel: "重新生成",
      tone: "warning",
    }))
  ) {
    return;
  }
  const apiKey = await generateReadOnlyApiKey();
  if (apiKey) readOnlyAPIKeyCard.value?.reveal(apiKey);
}

async function handleRevokeReadOnlyAPIKey() {
  if (
    !(await confirmAction({
      title: "废弃 API Key？",
      message: "废弃后，当前 API Key 会立即失效，外部 API 将无法访问。",
      confirmLabel: "确认废弃",
      tone: "error",
    }))
  ) {
    return;
  }
  await revokeReadOnlyApiKey();
}
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">额度管理</RouterLink></li>
          <li><h1>系统设置</h1></li>
        </ul>
      </div>
    </div>
  </PageShellHeader>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>
  <div v-if="success" class="col-span-12 alert alert-success">
    <AppIcon name="check-circle" class="size-5" />
    <span>{{ success }}</span>
  </div>
  <section v-if="loading" class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body items-center">
      <span class="loading loading-lg loading-spinner"></span>
    </div>
  </section>

  <fieldset
    v-if="settings && auth.isStaff"
    class="col-span-12 min-w-0 columns-1 gap-6 xl:columns-2 [&_.label]:whitespace-normal"
  >
    <Sub2APIConnectionCard
      v-model:settings="settings"
      v-model:admin-token="adminToken"
      :accounts="openAIAccounts"
      v-model:selected-test-account-id="selectedTestAccountId"
      :monitored-accounts="sub2apiMonitoredAccounts"
      :saving-account-id="savingAccountId"
      :loading-accounts="loadingAccounts"
      :testing="testing === 'sub2api'"
      :saving="saving === 'connection'"
      @load-accounts="loadOpenAIAccounts()"
      @test="test('sub2api')"
      @save="saveConnection"
      @save-account="saveMonitoredAccount"
    />
    <CPAConnectionCard
      v-model:settings="settings"
      v-model:management-key="cpaManagementKey"
      :accounts="cpaAccounts"
      :monitored-accounts="cpaMonitoredAccounts"
      :saving-account-id="savingAccountId"
      :loading-accounts="loadingCPAAccounts"
      :testing="testing === 'cpa'"
      :saving="saving === 'cpa'"
      @load-accounts="loadCPAAccounts()"
      @test="test('cpa')"
      @save="saveCPASettings"
      :save-pricing="saveCPAPricing"
      @save-account="saveMonitoredAccount"
    />
    <AllocationModelCard
      v-model:settings="settings"
      :saving="saving === 'allocation'"
      @save="saveAllocation"
    />
    <BillingCorrectionCard
      v-model:settings="settings"
      :saving="saving === 'billing-correction'"
      :save="saveBillingCorrection"
    />
    <SamplingStrategyCard
      v-model:settings="settings"
      :saving="saving === 'sampling'"
      @save="saveSampling"
    />
    <EmailServiceCard
      v-model:settings="settings"
      v-model:smtp-password="smtpPassword"
      v-model:resend-api-key="resendApiKey"
      :testing="testing === 'email'"
      :saving="saving === 'email'"
      @test="test('email')"
      @save="saveEmail"
    />
    <NotificationRulesCard
      v-model:settings="settings"
      :saving="saving === 'notifications'"
      @save="saveNotifications"
    />
    <DataMaintenanceCard
      v-model:account-id="maintenanceAccountId"
      :accounts="sub2apiMonitoredAccounts"
      :plan="historyRebuildPlan"
      :planning="planningHistory"
      :applying="applyingHistory"
      @create-plan="createHistoricalRebuildPlan"
      @apply="applyHistoricalRebuildPlan"
    />
    <DatabaseTransferCard
      :exporting="exportingDatabase"
      :importing="importingDatabase"
      :demo="demoMode"
      @export="exportDatabase"
      @import="importDatabase"
    />
    <LoginSecurityCard v-model:form="passwordForm" @change="changePassword" />
    <ReadOnlyAPIKeyCard
      v-if="auth.isStaff"
      ref="readOnlyAPIKeyCard"
      :configured="apiKeyConfigured"
      :hint="apiKeyHint"
      :created-at="apiKeyCreatedAt"
      :generating="generatingReadOnlyApiKey"
      :revoking="revokingReadOnlyApiKey"
      :full-access="true"
      :show-documentation="auth.canAccess('tutorial')"
      @generate="handleGenerateReadOnlyAPIKey"
      @revoke="handleRevokeReadOnlyAPIKey"
    />
  </fieldset>
  <div v-if="!auth.isStaff && personalApiKey" class="col-span-12 xl:col-span-6">
    <ReadOnlyAPIKeyCard
      ref="readOnlyAPIKeyCard"
      :configured="apiKeyConfigured"
      :hint="apiKeyHint"
      :created-at="apiKeyCreatedAt"
      :generating="generatingReadOnlyApiKey"
      :revoking="revokingReadOnlyApiKey"
      :full-access="false"
      :show-documentation="auth.canAccess('tutorial')"
      @generate="handleGenerateReadOnlyAPIKey"
      @revoke="handleRevokeReadOnlyAPIKey"
    />
  </div>
  <ConfirmDialog ref="confirmDialog" />
</template>
