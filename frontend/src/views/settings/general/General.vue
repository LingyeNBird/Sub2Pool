<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import {
  ApiError,
  api,
  apiBlob,
  clearAccessToken,
  jsonBody,
  setAccessToken,
} from "@/services/api";
import type { AppSettingsData, OpenAIAccountOption } from "@/types";

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
const databaseFile = ref<HTMLInputElement | null>(null);
const passwordForm = reactive({
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

function hasAccountOption(accountId: number | null) {
  return openAIAccounts.value.some((account) => account.id === accountId);
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
    if (settings.value.sub2api_token_configured) {
      await loadOpenAIAccounts(false);
    }
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "加载设置失败";
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
      body: jsonBody({
        ...settingsPayload(fields),
        ...secrets,
      }),
    });
    settings.value.sub2api_token_configured = updated.sub2api_token_configured;
    settings.value.smtp_password_configured = updated.smtp_password_configured;
    settings.value.resend_api_key_configured =
      updated.resend_api_key_configured;
    if (section === "connection") adminToken.value = "";
    if (section === "email") {
      smtpPassword.value = "";
      resendApiKey.value = "";
    }
    success.value = `${label}已保存`;
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "保存设置失败";
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
    message.value = error instanceof ApiError ? error.message : "修改密码失败";
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
          <li><h1>系统设置</h1></li>
        </ul>
      </div>
    </div>
  </PageShellHeader>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" /><span>{{
      message
    }}</span>
  </div>
  <div v-if="success" class="col-span-12 alert alert-success">
    <AppIcon name="check-circle" class="size-5" /><span>{{ success }}</span>
  </div>
  <section v-if="loading" class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body items-center">
      <span class="loading loading-lg loading-spinner"></span>
    </div>
  </section>

  <div v-if="settings" class="col-span-12 columns-1 gap-6 xl:columns-2">
    <section
      class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
    >
      <div class="card-body">
        <h2 class="card-title">
          <AppIcon name="code-bracket" class="size-5" />Sub2API 连接
        </h2>
        <fieldset class="fieldset">
          <label class="label">Sub2API 地址</label>
          <input
            v-model="settings.sub2api_base_url"
            type="url"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label">Admin Token</label>
          <input
            v-model="adminToken"
            type="password"
            class="input w-full"
            :placeholder="
              settings.sub2api_token_configured
                ? '已配置；留空保持不变'
                : '请输入 Admin Token'
            "
          />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label">OpenAI 上游账号</label>
          <div class="join w-full">
            <select
              v-model.number="settings.openai_account_id"
              class="select join-item grow"
            >
              <option :value="null">请选择 OpenAI 账号</option>
              <option
                v-if="
                  settings.openai_account_id &&
                  !hasAccountOption(settings.openai_account_id)
                "
                :value="settings.openai_account_id"
              >
                当前已保存账号（ID {{ settings.openai_account_id }}）
              </option>
              <option
                v-for="account in openAIAccounts"
                :key="account.id"
                :value="account.id"
              >
                {{ account.name }}（ID {{ account.id }} ·
                {{ account.status || "未知状态"
                }}{{ account.schedulable ? "" : " · 不可调度" }}）
              </option>
            </select>
            <button
              class="btn join-item"
              :disabled="loadingAccounts"
              @click="loadOpenAIAccounts()"
            >
              <span
                v-if="loadingAccounts"
                class="loading loading-xs loading-spinner"
              ></span>
              <AppIcon v-else name="arrow-path" class="size-4" />
              读取账号
            </button>
          </div>
          <p class="label">使用当前填写的地址和 Token 读取，不必先保存设置。</p>
        </fieldset>
        <fieldset class="fieldset">
          <label class="label">额度百分比查询方式</label>
          <select v-model="settings.quota_query_mode" class="select w-full">
            <option value="passive">
              被动：仅读取 Sub2API 已保存快照（默认）
            </option>
            <option value="direct">主动：调用上游账号额度接口</option>
          </select>
        </fieldset>
        <div
          v-if="settings.quota_query_mode === 'passive'"
          class="alert text-sm alert-info"
        >
          <AppIcon name="information-circle" class="size-5" />
          <span
            >不会请求 OpenAI 官方额度接口。快照由 Sub2API
            在正常转发请求中被动更新。</span
          >
        </div>
        <div v-else class="alert text-sm alert-warning">
          <AppIcon name="exclamation-triangle" class="size-5" />
          <span
            >主动模式会调用 OpenAI
            官方额度接口，频繁使用可能增加风控风险。</span
          >
        </div>
        <div class="grid gap-3 md:grid-cols-2">
          <fieldset class="fieldset">
            <label class="label">请求超时（秒）</label>
            <input
              v-model.number="settings.request_timeout_seconds"
              type="number"
              min="1"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">统计时区</label>
            <input v-model="settings.timezone" class="input w-full" />
          </fieldset>
        </div>
        <label class="label justify-between"
          >校验 HTTPS 证书<input
            v-model="settings.verify_tls"
            type="checkbox"
            class="toggle toggle-sm"
        /></label>
        <div class="flex flex-wrap gap-2">
          <button
            class="btn btn-sm"
            :disabled="testing === 'sub2api'"
            @click="test('sub2api')"
          >
            <span
              v-if="testing === 'sub2api'"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="signal" class="size-4" />测试连接
          </button>
          <button
            class="btn btn-primary btn-sm"
            :disabled="saving === 'connection'"
            @click="saveConnection"
          >
            <span
              v-if="saving === 'connection'"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="check" class="size-4" />保存连接设置
          </button>
        </div>
      </div>
    </section>

    <section
      class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
    >
      <div class="card-body">
        <h2 class="card-title">
          <AppIcon name="calculator" class="size-5" />分配模型
        </h2>
        <fieldset class="fieldset max-w-full min-w-0 grid-cols-[minmax(0,1fr)]">
          <label class="label">成本口径</label>
          <select
            v-model="settings.cost_basis"
            class="select w-full max-w-full min-w-0"
          >
            <option value="actual">实际扣费（推荐）</option>
            <option value="standard">标准计费</option>
          </select>
          <p class="label">
            实际扣费会应用 Sub2API
            的计费倍率，并与用户余额真实扣减一致；标准计费只按模型标准单价计算。余额分配建议通常应选择实际扣费。
          </p>
        </fieldset>
        <div class="grid gap-3 md:grid-cols-2">
          <fieldset class="fieldset min-w-0">
            <label class="label">无样本时美元 / 1%</label>
            <input
              v-model.number="settings.initial_usd_per_percent"
              type="number"
              min="0"
              step="0.01"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset min-w-0">
            <label class="label">安全系数</label>
            <input
              v-model.number="settings.safety_factor"
              type="number"
              min="0.1"
              max="1"
              step="0.01"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset min-w-0">
            <label class="label">保守分位数</label>
            <input
              v-model.number="settings.conservative_percentile"
              type="number"
              min="1"
              max="50"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset min-w-0">
            <label class="label">参与计算的历史样本数</label>
            <input
              v-model.number="settings.rate_history_samples"
              type="number"
              min="1"
              max="100"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset min-w-0 grid-cols-[minmax(0,1fr)]">
            <label class="label">日估算最小周限跨度（%）</label>
            <input
              v-model.number="settings.daily_estimate_min_percent_span"
              type="number"
              min="1"
              max="100"
              step="1"
              class="input w-full max-w-full min-w-0"
            />
            <p class="label">
              今日观测至少跨过该百分比后才给出日估算，避免整数周限造成过大误差。
            </p>
          </fieldset>
          <fieldset class="fieldset min-w-0">
            <label class="label">建议差额阈值（美元）</label>
            <input
              v-model.number="settings.recommendation_change_usd"
              type="number"
              min="0"
              step="0.01"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset min-w-0">
            <label class="label">用户余额耗尽预警余量（美元）</label>
            <input
              v-model.number="settings.limit_warning_usd"
              type="number"
              min="0"
              step="0.01"
              class="input w-full"
            />
          </fieldset>
        </div>
        <button
          class="btn btn-primary btn-sm"
          :disabled="saving === 'allocation'"
          @click="saveAllocation"
        >
          <span
            v-if="saving === 'allocation'"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="check" class="size-4" />保存分配模型
        </button>
      </div>
    </section>

    <section
      class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
    >
      <div class="card-body">
        <h2 class="card-title">
          <AppIcon name="clock" class="size-5" />采样策略
        </h2>
        <div class="grid gap-3 md:grid-cols-2">
          <fieldset class="fieldset">
            <label class="label">本地探测间隔（分钟）</label>
            <input
              v-model.number="settings.local_poll_minutes"
              type="number"
              min="2"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">进度触发阈值（%）</label>
            <input
              v-model.number="settings.progress_threshold_percent"
              type="number"
              min="0.1"
              max="10"
              step="0.1"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">活跃期最长校准间隔（小时）</label>
            <input
              v-model.number="settings.active_max_calibration_hours"
              type="number"
              min="1"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">重置前强制读取（分钟）</label>
            <input
              v-model.number="settings.reset_proximity_minutes"
              type="number"
              min="5"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">快照陈旧警告（小时）</label>
            <input
              v-model.number="settings.stale_warning_hours"
              type="number"
              min="1"
              class="input w-full"
            />
          </fieldset>
        </div>
        <label class="label justify-between"
          >启用后台监控<input
            v-model="settings.monitoring_enabled"
            type="checkbox"
            class="toggle toggle-sm"
        /></label>
        <p class="text-sm opacity-60">
          空闲时只做低频本地探测；达到成本进度、用户余额耗尽或重置条件后才形成新观测。
        </p>
        <button
          class="btn btn-primary btn-sm"
          :disabled="saving === 'sampling'"
          @click="saveSampling"
        >
          <span
            v-if="saving === 'sampling'"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="check" class="size-4" />保存采样策略
        </button>
      </div>
    </section>

    <section
      class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
    >
      <div class="card-body">
        <h2 class="card-title">
          <AppIcon name="envelope" class="size-5" />邮件服务
        </h2>
        <fieldset class="fieldset">
          <label class="label">发送方式</label>
          <select v-model="settings.email_provider" class="select w-full">
            <option value="smtp">SMTP</option>
            <option value="resend">Resend API</option>
          </select>
        </fieldset>
        <fieldset class="fieldset">
          <label class="label">接收通知邮箱</label>
          <input
            v-model="settings.notification_email"
            type="email"
            class="input w-full"
          />
        </fieldset>

        <template v-if="settings.email_provider === 'smtp'">
          <div class="grid gap-3 md:grid-cols-2">
            <fieldset class="fieldset">
              <label class="label">SMTP 主机</label>
              <input v-model="settings.smtp_host" class="input w-full" />
            </fieldset>
            <fieldset class="fieldset">
              <label class="label">端口</label>
              <input
                v-model.number="settings.smtp_port"
                type="number"
                min="1"
                class="input w-full"
              />
            </fieldset>
            <fieldset class="fieldset">
              <label class="label">用户名</label>
              <input v-model="settings.smtp_username" class="input w-full" />
            </fieldset>
            <fieldset class="fieldset">
              <label class="label">密码</label>
              <input
                v-model="smtpPassword"
                type="password"
                class="input w-full"
                :placeholder="
                  settings.smtp_password_configured
                    ? '已配置；留空保持不变'
                    : '请输入 SMTP 密码'
                "
              />
            </fieldset>
            <fieldset class="fieldset md:col-span-2">
              <label class="label">SMTP 发件人</label>
              <input
                v-model="settings.smtp_from_email"
                type="email"
                class="input w-full"
              />
            </fieldset>
          </div>
          <label class="label justify-between"
            >STARTTLS<input
              v-model="settings.smtp_use_tls"
              type="checkbox"
              class="toggle toggle-sm"
              @change="
                settings.smtp_use_tls && (settings.smtp_use_ssl = false)
              "
          /></label>
          <label class="label justify-between"
            >直接 SSL<input
              v-model="settings.smtp_use_ssl"
              type="checkbox"
              class="toggle toggle-sm"
              @change="
                settings.smtp_use_ssl && (settings.smtp_use_tls = false)
              "
          /></label>
        </template>

        <template v-else>
          <fieldset class="fieldset">
            <label class="label">Resend API Key</label>
            <input
              v-model="resendApiKey"
              type="password"
              class="input w-full"
              :placeholder="
                settings.resend_api_key_configured
                  ? '已配置；留空保持不变'
                  : '请输入 re_ 开头的 API Key'
              "
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">Resend 发件人</label>
            <input
              v-model="settings.resend_from_email"
              class="input w-full"
              placeholder="拼车额度 &lt;notice@example.com&gt;"
            />
          </fieldset>
          <div class="alert text-sm alert-info">
            <AppIcon name="information-circle" class="size-5" />
            <span>
              发件人支持“名称 &lt;邮箱&gt;”格式；正式发送前需要在 Resend
              中验证对应域名。
            </span>
          </div>
        </template>

        <div class="flex flex-wrap gap-2">
          <button
            class="btn btn-sm"
            :disabled="testing === 'email'"
            @click="test('email')"
          >
            <span
              v-if="testing === 'email'"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="paper-airplane" class="size-4" />发送测试邮件
          </button>
          <button
            class="btn btn-primary btn-sm"
            :disabled="saving === 'email'"
            @click="saveEmail"
          >
            <span
              v-if="saving === 'email'"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="check" class="size-4" />保存邮件设置
          </button>
        </div>
      </div>
    </section>

    <section
      class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
    >
      <div class="card-body">
        <h2 class="card-title">
          <AppIcon name="bell-alert" class="size-5" />通知规则
        </h2>
        <label class="label justify-between"
          >参与者用户余额耗尽时通知<input
            v-model="settings.notify_on_limit_exhausted"
            type="checkbox"
            class="toggle toggle-sm"
        /></label>
        <label class="label justify-between"
          >建议金额明显变化时通知<input
            v-model="settings.notify_on_recommendation_change"
            type="checkbox"
            class="toggle toggle-sm"
        /></label>
        <label class="label justify-between"
          >美元 / 百分比明显变化时通知<input
            v-model="settings.notify_on_rate_change"
            type="checkbox"
            class="toggle toggle-sm"
        /></label>
        <label class="label justify-between"
          >采集失败时通知<input
            v-model="settings.notify_on_collection_error"
            type="checkbox"
            class="toggle toggle-sm"
        /></label>
        <div class="grid gap-3 md:grid-cols-2">
          <fieldset class="fieldset">
            <label class="label">汇率变化阈值（%）</label>
            <input
              v-model.number="settings.rate_change_alert_percent"
              type="number"
              min="0"
              step="0.1"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">相同通知冷却（分钟）</label>
            <input
              v-model.number="settings.notification_cooldown_minutes"
              type="number"
              min="1"
              class="input w-full"
            />
          </fieldset>
        </div>
        <button
          class="btn btn-primary btn-sm"
          :disabled="saving === 'notifications'"
          @click="saveNotifications"
        >
          <span
            v-if="saving === 'notifications'"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="check" class="size-4" />保存通知规则
        </button>
      </div>
    </section>

    <section
      class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
    >
      <div class="card-body">
        <h2 class="card-title">
          <AppIcon name="circle-stack" class="size-5" />数据库迁移
        </h2>
        <p class="text-sm leading-6 opacity-70">
          导出文件包含参与者、账本、统计、通知、登录记录、管理员账号以及全部系统设置。
          导入会完整覆盖当前数据库，并在服务器数据目录保留
          pinche.before-import.sqlite3 作为覆盖前副本。
        </p>
        <div class="alert text-sm alert-warning">
          <AppIcon name="exclamation-triangle" class="size-5" />
          <span>
            加密后的 Admin Token、SMTP 密码和 Resend Key 依赖部署环境中的
            DJANGO_SECRET_KEY。迁移服务器时还必须安全复制
            .env，数据库备份不会包含环境变量。
          </span>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            class="btn btn-sm"
            :disabled="exportingDatabase || importingDatabase"
            @click="exportDatabase"
          >
            <span
              v-if="exportingDatabase"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="arrow-down-tray" class="size-4" />
            导出完整数据库
          </button>
          <button
            class="btn btn-outline btn-error btn-sm"
            :disabled="importingDatabase || exportingDatabase"
            @click="databaseFile?.click()"
          >
            <span
              v-if="importingDatabase"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="arrow-up-tray" class="size-4" />
            导入并覆盖
          </button>
          <input
            ref="databaseFile"
            type="file"
            accept=".sqlite3,.sqlite,.db,application/vnd.sqlite3"
            class="hidden"
            @change="importDatabase"
          />
        </div>
      </div>
    </section>

    <section
      class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
    >
      <div class="card-body">
        <h2 class="card-title">
          <AppIcon name="lock-closed" class="size-5" />登录安全
        </h2>
        <fieldset class="fieldset">
          <label class="label">当前密码</label>
          <input
            v-model="passwordForm.old_password"
            type="password"
            class="input w-full"
            autocomplete="current-password"
          />
        </fieldset>
        <div class="grid gap-3 md:grid-cols-2">
          <fieldset class="fieldset">
            <label class="label">新密码</label>
            <input
              v-model="passwordForm.new_password"
              type="password"
              class="input w-full"
              autocomplete="new-password"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">确认新密码</label>
            <input
              v-model="passwordForm.confirm_password"
              type="password"
              class="input w-full"
              autocomplete="new-password"
            />
          </fieldset>
        </div>
        <button class="btn btn-sm" @click="changePassword">修改密码</button>
        <p class="text-sm opacity-60">
          站点密钥、Cookie Secure 和允许域名属于部署安全边界，仅通过 Docker
          环境变量配置。
        </p>
      </div>
    </section>
  </div>
</template>
