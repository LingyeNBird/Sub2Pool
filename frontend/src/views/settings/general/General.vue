<script setup lang="ts">
import { ref } from "vue";
import PageShellHeader from "@/components/common/PageShellHeader.vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import type { ConfirmDialogHandle, ConfirmDialogOptions } from "@/types";

import AllocationModelCard from "./components/AllocationModelCard.vue";
import DatabaseTransferCard from "./components/DatabaseTransferCard.vue";
import DataMaintenanceCard from "./components/DataMaintenanceCard.vue";
import EmailServiceCard from "./components/EmailServiceCard.vue";
import FastCorrectionCard from "./components/FastCorrectionCard.vue";
import FastCorrectionRebuildDialog, {
  type FastCorrectionRebuildScope,
} from "./components/FastCorrectionRebuildDialog.vue";
import LoginSecurityCard from "./components/LoginSecurityCard.vue";
import ReadOnlyAPIKeyCard from "./components/ReadOnlyAPIKeyCard.vue";
import NotificationRulesCard from "./components/NotificationRulesCard.vue";
import SamplingStrategyCard from "./components/SamplingStrategyCard.vue";
import Sub2APIConnectionCard from "./components/Sub2APIConnectionCard.vue";
import { useSettingsPage } from "./composables/useSettingsPage";

const confirmDialog = ref<ConfirmDialogHandle | null>(null);

function confirmAction(options: ConfirmDialogOptions) {
  return confirmDialog.value?.open(options) ?? Promise.resolve(false);
}

const {
  settings,
  loading,
  saving,
  testing,
  message,
  success,
  adminToken,
  smtpPassword,
  resendApiKey,
  openAIAccounts,
  loadingAccounts,
  exportingDatabase,
  importingDatabase,
  rebuildingFastCorrection,
  historyPreview,
  generatingReadOnlyApiKey,
  revokingReadOnlyApiKey,
  checkingHistoricalUsage,
  backfillingHistoricalUsage,
  rebuildingAllParticles,
  costHistoryPreview,
  checkingCostHistory,
  repairingCostHistory,
  passwordForm,
  loadOpenAIAccounts,
  saveConnection,
  saveAllocation,
  saveSampling,
  saveEmail,
  saveFastCorrection,
  rebuildFastCorrection,
  saveNotifications,
  exportDatabase,
  importDatabase,
  previewHistoricalUsage,
  backfillHistoricalUsage,
  rebuildAllParticles,
  previewCostHistory,
  repairCostHistory,
  test,
  generateReadOnlyApiKey,
  revokeReadOnlyApiKey,
  changePassword,
} = useSettingsPage(confirmAction);

const fastCorrectionDialog = ref<InstanceType<
  typeof FastCorrectionRebuildDialog
> | null>(null);

async function handleFastCorrectionSave() {
  if (await saveFastCorrection()) {
    fastCorrectionDialog.value?.open(true);
  }
}

async function handleFastCorrectionRebuild(scope: FastCorrectionRebuildScope) {
  if (await rebuildFastCorrection(scope)) {
    fastCorrectionDialog.value?.close();
  }
}

const readOnlyAPIKeyCard = ref<InstanceType<typeof ReadOnlyAPIKeyCard> | null>(
  null,
);

async function handleGenerateReadOnlyAPIKey() {
  if (
    settings.value?.readonly_api_key_configured &&
    !(await confirmAction({
      title: "重新生成只读 API Key？",
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
      title: "废弃只读 API Key？",
      message: "废弃后，当前 API Key 会立即失效，外部只读接口将无法访问。",
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

  <div v-if="settings" class="col-span-12 columns-1 gap-6 xl:columns-2">
    <Sub2APIConnectionCard
      v-model:settings="settings"
      v-model:admin-token="adminToken"
      :accounts="openAIAccounts"
      :loading-accounts="loadingAccounts"
      :testing="testing === 'sub2api'"
      :saving="saving === 'connection'"
      @load-accounts="loadOpenAIAccounts()"
      @test="test('sub2api')"
      @save="saveConnection"
    />
    <AllocationModelCard
      v-model:settings="settings"
      :saving="saving === 'allocation'"
      @save="saveAllocation"
    />
    <FastCorrectionCard
      v-model:settings="settings"
      :saving="saving === 'fast-correction'"
      :rebuilding="rebuildingFastCorrection"
      @save="handleFastCorrectionSave"
      @rebuild="fastCorrectionDialog?.open()"
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
      :preview="historyPreview"
      :checking="checkingHistoricalUsage"
      :backfilling="backfillingHistoricalUsage"
      :rebuilding="rebuildingAllParticles"
      :cost-preview="costHistoryPreview"
      :checking-cost="checkingCostHistory"
      :repairing-cost="repairingCostHistory"
      @preview="previewHistoricalUsage"
      @backfill="backfillHistoricalUsage"
      @rebuild="rebuildAllParticles"
      @cost-preview="previewCostHistory"
      @cost-repair="repairCostHistory"
    />
    <DatabaseTransferCard
      :exporting="exportingDatabase"
      :importing="importingDatabase"
      @export="exportDatabase"
      @import="importDatabase"
    />
    <LoginSecurityCard v-model:form="passwordForm" @change="changePassword" />
    <ReadOnlyAPIKeyCard
      ref="readOnlyAPIKeyCard"
      :configured="settings.readonly_api_key_configured"
      :hint="settings.readonly_api_key_hint"
      :created-at="settings.readonly_api_key_created_at"
      :generating="generatingReadOnlyApiKey"
      :revoking="revokingReadOnlyApiKey"
      @generate="handleGenerateReadOnlyAPIKey"
      @revoke="handleRevokeReadOnlyAPIKey"
    />
  </div>
  <FastCorrectionRebuildDialog
    ref="fastCorrectionDialog"
    :rebuilding="rebuildingFastCorrection"
    @confirm="handleFastCorrectionRebuild"
  />
  <ConfirmDialog ref="confirmDialog" />
</template>
