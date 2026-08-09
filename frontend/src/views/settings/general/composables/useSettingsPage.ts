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
import type {
  AppSettingsData,
  FastCorrectionRebuildResult,
  OpenAIAccountOption,
} from "@/types";

export interface PasswordForm {
  old_password: string;
  new_password: string;
  confirm_password: string;
}

export function useSettingsPage() {
  const auth = useAuthStore();
  const settings = ref<AppSettingsData | null>(null);
  const loading = ref(true);
  const saving = ref("");
  const testing = ref("");
  const message = ref("");
  const success = ref("");
  const adminToken = ref("");
  const smtpPassword = ref("");
  const resendApiKey = ref("");
  const openAIAccounts = ref<OpenAIAccountOption[]>([]);
  const loadingAccounts = ref(false);
  const exportingDatabase = ref(false);
  const importingDatabase = ref(false);
  const rebuildingFastCorrection = ref(false);
  const savedFastCorrectionEnabled = ref(true);
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
      openai_account_id: settings.value.openai_account_id,
      quota_query_mode: settings.value.quota_query_mode,
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

  async function load() {
    loading.value = true;
    try {
      settings.value = await api<AppSettingsData>("settings");
      savedFastCorrectionEnabled.value = settings.value.fast_correction_enabled;
      if (settings.value.sub2api_token_configured) {
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
      [
        "sub2api_base_url",
        "openai_account_id",
        "quota_query_mode",
        "request_timeout_seconds",
        "verify_tls",
        "timezone",
      ],
      { sub2api_admin_token: adminToken.value },
    );
  }

  function saveAllocation() {
    return saveSection("allocation", "分配模型设置", [
      "cost_basis",
      "weekly_quota_model",
      "initial_usd_per_percent",
      "safety_factor",
      "conservative_percentile",
      "rate_history_samples",
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
    const wasEnabled = savedFastCorrectionEnabled.value;
    try {
      const updated = await api<AppSettingsData>("settings", {
        method: "PATCH",
        body: jsonBody({
          fast_correction_enabled: settings.value.fast_correction_enabled,
        }),
      });
      settings.value.fast_correction_enabled = updated.fast_correction_enabled;
      settings.value.fast_correction_rebuild_recommended =
        updated.fast_correction_rebuild_recommended;
      settings.value.fast_correction_missing_intervals =
        updated.fast_correction_missing_intervals;
      savedFastCorrectionEnabled.value = updated.fast_correction_enabled;
      success.value = "FAST 修正设置已保存";
      return Boolean(
        !wasEnabled &&
        updated.fast_correction_enabled &&
        updated.fast_correction_rebuild_recommended,
      );
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "保存 FAST 修正设置失败";
      return false;
    } finally {
      saving.value = "";
    }
  }

  async function rebuildFastCorrection(scope: "cycle" | "all") {
    if (!settings.value) return false;
    rebuildingFastCorrection.value = true;
    message.value = "";
    success.value = "";
    try {
      const result = await api<FastCorrectionRebuildResult>(
        "settings/fast-correction/rebuild",
        {
          method: "POST",
          body: jsonBody({ scope }),
        },
      );
      settings.value.fast_correction_rebuild_recommended = false;
      settings.value.fast_correction_missing_intervals = 0;
      success.value = `FAST 修正重建完成：处理 ${result.rebuilt_observations} 个采样区间，识别 ${result.fast_request_count} 条 FAST 请求，补充 ${result.correction_usd.toFixed(2)} 美元等效用量。`;
      return true;
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "FAST 修正重建失败";
      return false;
    } finally {
      rebuildingFastCorrection.value = false;
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
      anchor.download = `pinche-backup-${new Date()
        .toISOString()
        .replaceAll(":", "-")}.sqlite3`;
      anchor.click();
      URL.revokeObjectURL(url);
      success.value = "数据库备份已导出";
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "导出数据库失败";
    } finally {
      exportingDatabase.value = false;
    }
  }

  async function importDatabase(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    if (
      !window.confirm("导入会完整覆盖当前数据库，并要求重新登录。确认继续吗？")
    ) {
      input.value = "";
      return;
    }

    importingDatabase.value = true;
    message.value = "";
    success.value = "";
    try {
      const form = new FormData();
      form.append("database", file);
      await api("database/import", { method: "POST", body: form });
      clearAccessToken();
      window.location.assign("/login");
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "导入数据库失败";
    } finally {
      importingDatabase.value = false;
      input.value = "";
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
      success.value =
        kind === "sub2api" ? "Sub2API 连接与额度读取正常" : "测试邮件已发送";
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
      success.value = "登录密码已修改";
    } catch (error) {
      message.value =
        error instanceof ApiError ? error.message : "修改密码失败";
    }
  }

  onMounted(load);

  return {
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
    passwordForm,
    loadOpenAIAccounts,
    saveConnection,
    saveAllocation,
    saveSampling,
    saveEmail,
    saveNotifications,
    exportDatabase,
    importDatabase,
    saveFastCorrection,
    rebuildFastCorrection,
    test,
    changePassword,
  };
}
