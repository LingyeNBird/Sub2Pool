import { onMounted, reactive, ref } from "vue";

import {
  ApiError,
  api,
  apiBlob,
  clearAccessToken,
  jsonBody,
  setAccessToken,
} from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { OpenAIAccountOption, MonitoredAccount } from "@/types/accounts";
import type { ConfirmDialogOptions } from "@/types/common";
import type {
  APIKeyState,
  AppSettingsData,
  HistoricalRebuildPlan,
  ReadOnlyAPIKeyGenerated,
} from "@/types/settings";

export interface PasswordForm {
  old_password: string;
  new_password: string;
  confirm_password: string;
}

type ConfirmAction = (options: ConfirmDialogOptions) => Promise<boolean>;

export function useSettingsPage(confirmAction: ConfirmAction) {
  const demoMode = import.meta.env.VITE_DEMO_MODE === "true";
  const auth = useAuthStore();
  const settings = ref<AppSettingsData | null>(null);
  const personalApiKey = ref<APIKeyState | null>(null);
  const loading = ref(true);
  const saving = ref("");
  const testing = ref("");
  const message = ref("");
  const success = ref("");
  const adminToken = ref("");
  const smtpPassword = ref("");
  const resendApiKey = ref("");
  const openAIAccounts = ref<OpenAIAccountOption[]>([]);
  const monitoredAccounts = ref<MonitoredAccount[]>([]);
  const selectedTestAccountId = ref<number | null>(null);
  const maintenanceAccountId = ref<number | null>(null);
  const savingAccountId = ref<number | "new" | null>(null);
  const loadingAccounts = ref(false);
  const exportingDatabase = ref(false);
  const importingDatabase = ref(false);
  const historyRebuildPlan = ref<HistoricalRebuildPlan | null>(null);
  const planningHistory = ref(false);
  const applyingHistory = ref(false);
  const generatingReadOnlyApiKey = ref(false);
  const revokingReadOnlyApiKey = ref(false);
  const passwordForm = reactive<PasswordForm>({
    old_password: "",
    new_password: "",
    confirm_password: "",
  });

  function connectionPayload() {
    if (!settings.value) return {};
    return {
      sub2api_base_url: settings.value.sub2api_base_url,
      sub2api_admin_token: adminToken.value,
      openai_account_id: selectedTestAccountId.value,
      quota_query_mode:
        monitoredAccounts.value.find(
          (item) => item.id === selectedTestAccountId.value,
        )?.quota_query_mode ?? "passive",
      request_timeout_seconds: settings.value.request_timeout_seconds,
      verify_tls: settings.value.verify_tls,
    };
  }

  async function loadOpenAIAccounts(announce = true) {
    if (!settings.value) return;
    loadingAccounts.value = true;
    if (announce) {
      message.value = "";
      success.value = "";
    }
    try {
      openAIAccounts.value = await api<OpenAIAccountOption[]>(
        "settings/openai-accounts",
        {
          method: "POST",
          body: jsonBody(connectionPayload()),
        },
      );
      if (announce) {
        success.value = `已读取 ${openAIAccounts.value.length} 个 OpenAI 账号`;
      }
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "读取 OpenAI 账号失败";
    } finally {
      loadingAccounts.value = false;
    }
  }

  async function loadMonitoredAccounts() {
    monitoredAccounts.value = await api<MonitoredAccount[]>(
      "settings/monitored-accounts",
    );
    const firstEnabled =
      monitoredAccounts.value.find((item) => item.enabled) ??
      monitoredAccounts.value[0] ??
      null;
    if (
      !monitoredAccounts.value.some(
        (item) => item.id === selectedTestAccountId.value,
      )
    ) {
      selectedTestAccountId.value = firstEnabled?.id ?? null;
    }
    if (
      !monitoredAccounts.value.some(
        (item) => item.id === maintenanceAccountId.value,
      )
    ) {
      maintenanceAccountId.value = firstEnabled?.id ?? null;
    }
  }

  async function load() {
    loading.value = true;
    try {
      settings.value = await api<AppSettingsData>("settings");
      if (!auth.isStaff) {
        personalApiKey.value = await api<APIKeyState>("settings/my-api-key");
      }
      await loadMonitoredAccounts();
      if (auth.isStaff && settings.value.sub2api_token_configured) {
        await loadOpenAIAccounts(false);
      }
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "加载设置失败";
    } finally {
      loading.value = false;
    }
  }

  function settingsPayload(fields: string[]) {
    if (!settings.value) return {};
    return Object.fromEntries(
      fields.map((field) => [field, settings.value?.[field]]),
    );
  }

  async function saveSection(
    section: string,
    label: string,
    fields: string[],
    secrets: Record<string, string> = {},
  ) {
    if (!settings.value) return;
    saving.value = section;
    message.value = "";
    success.value = "";
    try {
      const updated = await api<AppSettingsData>("settings", {
        method: "PATCH",
        body: jsonBody({ ...settingsPayload(fields), ...secrets }),
      });
      settings.value.sub2api_token_configured =
        updated.sub2api_token_configured;
      settings.value.smtp_password_configured =
        updated.smtp_password_configured;
      settings.value.resend_api_key_configured =
        updated.resend_api_key_configured;
      if (fields.includes("timezone")) auth.setTimezone(updated.timezone);
      if (section === "connection") adminToken.value = "";
      if (section === "email") {
        smtpPassword.value = "";
        resendApiKey.value = "";
      }
      if (section === "connection" || section === "allocation") {
        historyRebuildPlan.value = null;
      }
      success.value = `${label}已保存`;
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "保存设置失败";
    } finally {
      saving.value = "";
    }
  }

  function saveConnection() {
    return saveSection(
      "connection",
      "Sub2API 连接设置",
      ["sub2api_base_url", "request_timeout_seconds", "verify_tls", "timezone"],
      { sub2api_admin_token: adminToken.value },
    );
  }

  async function saveMonitoredAccount(
    account: Pick<
      MonitoredAccount,
      "id" | "external_account_id" | "name" | "enabled" | "quota_query_mode"
    >,
    create = false,
  ) {
    savingAccountId.value = create ? "new" : account.id;
    message.value = "";
    success.value = "";
    try {
      await api(
        create
          ? "settings/monitored-accounts"
          : `settings/monitored-accounts/${account.id}`,
        {
          method: create ? "POST" : "PUT",
          body: jsonBody({
            external_account_id: account.external_account_id,
            name: account.name,
            enabled: account.enabled,
            quota_query_mode: account.quota_query_mode,
          }),
        },
      );
      await loadMonitoredAccounts();
      historyRebuildPlan.value = null;
      success.value = create ? "监控账号已添加" : "监控账号已保存";
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "保存监控账号失败";
    } finally {
      savingAccountId.value = null;
    }
  }

  async function removeMonitoredAccount(account: MonitoredAccount) {
    if (
      !(await confirmAction({
        title: `删除监控账号“${account.name}”？`,
        message: "已有历史事实的账号不能删除，只能停用。",
        confirmLabel: "删除",
        tone: "error",
      }))
    ) {
      return;
    }
    savingAccountId.value = account.id;
    message.value = "";
    success.value = "";
    try {
      await api(`settings/monitored-accounts/${account.id}`, {
        method: "DELETE",
      });
      await loadMonitoredAccounts();
      historyRebuildPlan.value = null;
      success.value = "监控账号已删除";
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "删除监控账号失败";
    } finally {
      savingAccountId.value = null;
    }
  }

  function saveAllocation() {
    return saveSection("allocation", "分配模型设置", [
      "cost_basis",
      "weekly_quota_model",
      "initial_usd_per_percent",
      "safety_factor",
      "daily_estimate_min_percent_span",
      "recommendation_change_usd",
      "limit_warning_usd",
    ]);
  }

  function saveSampling() {
    return saveSection("sampling", "采样策略设置", [
      "local_poll_minutes",
      "progress_threshold_percent",
      "active_max_calibration_hours",
      "reset_proximity_minutes",
      "stale_warning_hours",
      "monitoring_enabled",
    ]);
  }

  async function saveFastCorrection() {
    if (!settings.value) return false;
    saving.value = "fast-correction";
    message.value = "";
    success.value = "";
    try {
      const updated = await api<AppSettingsData>("settings", {
        method: "PATCH",
        body: jsonBody({
          fast_correction_enabled: settings.value.fast_correction_enabled,
          fast_correction_rules: settings.value.fast_correction_rules,
        }),
      });
      settings.value.fast_correction_enabled = updated.fast_correction_enabled;
      settings.value.fast_correction_rules = updated.fast_correction_rules;
      settings.value.fast_correction_rebuild_recommended =
        updated.fast_correction_rebuild_recommended;
      settings.value.fast_correction_missing_intervals =
        updated.fast_correction_missing_intervals;
      historyRebuildPlan.value = null;
      success.value = "FAST 修正设置已保存";
      return true;
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "保存 FAST 修正设置失败";
      return false;
    } finally {
      saving.value = "";
    }
  }

  function saveEmail() {
    return saveSection(
      "email",
      "邮件服务设置",
      [
        "email_provider",
        "notification_email",
        "smtp_host",
        "smtp_port",
        "smtp_username",
        "smtp_use_tls",
        "smtp_use_ssl",
        "smtp_from_email",
        "resend_from_email",
      ],
      {
        smtp_password: smtpPassword.value,
        resend_api_key: resendApiKey.value,
      },
    );
  }

  function saveNotifications() {
    return saveSection("notifications", "通知规则设置", [
      "notify_on_limit_exhausted",
      "notify_on_recommendation_change",
      "notify_on_rate_change",
      "notify_on_collection_error",
      "rate_change_alert_percent",
      "notification_cooldown_minutes",
    ]);
  }

  async function exportDatabase() {
    exportingDatabase.value = true;
    message.value = "";
    success.value = "";
    try {
      const blob = await apiBlob("database/export");
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = demoMode
        ? `sub2pool-demo-${new Date().toISOString().slice(0, 10)}.json`
        : `pinche-backup-${new Date()
            .toISOString()
            .replaceAll(":", "-")}.sqlite3`;
      anchor.click();
      URL.revokeObjectURL(url);
      success.value = demoMode
        ? "合成演示数据已导出；该文件不是数据库备份"
        : "数据库备份已导出";
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "导出数据库失败";
    } finally {
      exportingDatabase.value = false;
    }
  }

  async function importDatabase(event: Event) {
    const input = event.target as HTMLInputElement | null;
    const file = input?.files?.[0];
    if (!demoMode && !file) return;
    if (
      !(await confirmAction({
        title: demoMode ? "重置演示数据？" : "覆盖当前数据库？",
        message: demoMode
          ? "演示站不会读取数据库文件；此操作只会把当前标签页恢复到初始合成数据。"
          : "导入会完整覆盖当前数据库，并要求所有用户重新登录。覆盖前会在服务器数据目录保留一份当前数据库副本。",
        confirmLabel: demoMode ? "确认重置" : "导入并覆盖",
        tone: demoMode ? "warning" : "error",
      }))
    ) {
      if (input) input.value = "";
      return;
    }

    importingDatabase.value = true;
    message.value = "";
    success.value = "";
    try {
      if (demoMode) {
        await api("database/import", { method: "POST" });
        success.value = "演示数据已恢复到初始状态";
        window.dispatchEvent(new CustomEvent("sub2pool:demo-reset"));
        window.location.reload();
        return;
      }
      const form = new FormData();
      form.append("database", file!);
      await api("database/import", { method: "POST", body: form });
      clearAccessToken();
      window.location.assign("/login");
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "导入数据库失败";
    } finally {
      importingDatabase.value = false;
      if (input) input.value = "";
    }
  }

  async function createHistoricalRebuildPlan() {
    if (maintenanceAccountId.value == null) {
      message.value = "请先选择要维护的监控账号";
      return;
    }
    planningHistory.value = true;
    message.value = "";
    success.value = "";
    historyRebuildPlan.value = null;
    try {
      const plan = await api<HistoricalRebuildPlan>(
        "settings/data-maintenance/history-rebuild-plans",
        {
          method: "POST",
          body: jsonBody({ account_id: maintenanceAccountId.value }),
        },
      );
      historyRebuildPlan.value = plan;
      if (plan.state === "ready") {
        success.value =
          "本地全点审计完成：计划已冻结，应用阶段不会连接 Sub2API。";
      } else if (plan.state === "blocked") {
        success.value = "计划已保存但被源事实不变量阻断。";
      } else {
        success.value = `计划状态：${plan.state}`;
      }
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "创建历史维护计划失败";
    } finally {
      planningHistory.value = false;
    }
  }

  async function applyHistoricalRebuildPlan() {
    const plan = historyRebuildPlan.value;
    if (!plan?.safe_to_apply) return;
    if (
      !(await confirmAction({
        title: "应用已冻结的历史维护计划？",
        message:
          "系统只消费当前 plan id 与 digest，应用阶段不会访问 Sub2API，也不会改写来源成本；通过审计后只确定性重放派生结果。",
        confirmLabel: "应用计划",
        tone: "warning",
      }))
    ) {
      return;
    }
    applyingHistory.value = true;
    message.value = "";
    success.value = "";
    try {
      historyRebuildPlan.value = await api<HistoricalRebuildPlan>(
        `settings/data-maintenance/history-rebuild-plans/${plan.id}/apply`,
        { method: "POST", body: jsonBody({ digest: plan.digest }) },
      );
      const replay = historyRebuildPlan.value.replay_summary;
      success.value =
        replay.rebuilt_observations !== undefined
          ? `计划已应用：重放 ${replay.rebuilt_observations} 条观测，fact revision 为 ${historyRebuildPlan.value.result_revision}。`
          : "计划已应用。";
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "应用历史维护计划失败";
      try {
        historyRebuildPlan.value = await api<HistoricalRebuildPlan>(
          `settings/data-maintenance/history-rebuild-plans/${plan.id}`,
        );
      } catch {
        historyRebuildPlan.value = null;
      }
    } finally {
      applyingHistory.value = false;
    }
  }

  async function generateReadOnlyApiKey(): Promise<string | null> {
    if (!settings.value) return null;
    generatingReadOnlyApiKey.value = true;
    message.value = "";
    success.value = "";
    try {
      const generated = await api<ReadOnlyAPIKeyGenerated>(
        auth.isStaff ? "settings/readonly-api-key" : "settings/my-api-key",
        { method: "POST" },
      );
      if (auth.isStaff) {
        settings.value.readonly_api_key_configured = true;
        settings.value.readonly_api_key_hint = generated.hint;
        settings.value.readonly_api_key_created_at = generated.created_at;
      } else {
        personalApiKey.value = {
          configured: true,
          hint: generated.hint,
          created_at: generated.created_at,
        };
      }
      success.value = "API Key 已生成";
      return generated.api_key;
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "生成 API Key 失败";
      return null;
    } finally {
      generatingReadOnlyApiKey.value = false;
    }
  }

  async function revokeReadOnlyApiKey(): Promise<boolean> {
    if (!settings.value) return false;
    revokingReadOnlyApiKey.value = true;
    message.value = "";
    success.value = "";
    try {
      await api(
        auth.isStaff ? "settings/readonly-api-key" : "settings/my-api-key",
        { method: "DELETE" },
      );
      if (auth.isStaff) {
        settings.value.readonly_api_key_configured = false;
        settings.value.readonly_api_key_hint = "";
        settings.value.readonly_api_key_created_at = null;
      } else {
        personalApiKey.value = {
          configured: false,
          hint: "",
          created_at: null,
        };
      }
      success.value = "API Key 已废弃";
      return true;
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "废弃 API Key 失败";
      return false;
    } finally {
      revokingReadOnlyApiKey.value = false;
    }
  }

  async function test(kind: "sub2api" | "email") {
    testing.value = kind;
    message.value = "";
    success.value = "";
    try {
      await api(`settings/test-${kind}`, {
        method: "POST",
        body: kind === "sub2api" ? jsonBody(connectionPayload()) : undefined,
      });
      success.value = demoMode
        ? kind === "sub2api"
          ? "演示连接检查成功；未发起任何网络连接"
          : "演示邮件检查成功；未发送真实邮件"
        : kind === "sub2api"
          ? "Sub2API 连接与额度读取正常"
          : "测试邮件已发送";
    } catch (error) {
      message.value = error instanceof ApiError ? error.message : "测试失败";
    } finally {
      testing.value = "";
    }
  }

  async function changePassword() {
    message.value = "";
    success.value = "";
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      message.value = "两次输入的新密码不一致";
      return;
    }
    try {
      const result = await api<{ changed: boolean; access: string }>(
        "auth/password",
        {
          method: "POST",
          body: jsonBody(passwordForm),
        },
      );
      setAccessToken(result.access);
      Object.assign(passwordForm, {
        old_password: "",
        new_password: "",
        confirm_password: "",
      });
      success.value = demoMode
        ? "演示表单校验完成；未修改任何真实凭据"
        : "登录密码已修改";
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "修改密码失败";
    }
  }

  onMounted(load);

  return {
    settings,
    personalApiKey,
    loading,
    saving,
    testing,
    message,
    success,
    adminToken,
    smtpPassword,
    resendApiKey,
    openAIAccounts,
    monitoredAccounts,
    selectedTestAccountId,
    maintenanceAccountId,
    savingAccountId,
    loadingAccounts,
    exportingDatabase,
    importingDatabase,
    historyRebuildPlan,
    planningHistory,
    applyingHistory,
    generatingReadOnlyApiKey,
    revokingReadOnlyApiKey,
    passwordForm,
    loadOpenAIAccounts,
    loadMonitoredAccounts,
    saveConnection,
    saveMonitoredAccount,
    removeMonitoredAccount,
    saveAllocation,
    saveSampling,
    saveEmail,
    saveNotifications,
    exportDatabase,
    importDatabase,
    saveFastCorrection,
    createHistoricalRebuildPlan,
    applyHistoricalRebuildPlan,
    test,
    changePassword,
    generateReadOnlyApiKey,
    revokeReadOnlyApiKey,
  };
}
