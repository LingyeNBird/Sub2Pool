<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api, jsonBody, setAccessToken } from "@/services/api";
import type { AppSettingsData } from "@/types";

const settings = ref<AppSettingsData | null>(null);
const loading = ref(true);
const saving = ref(false);
const testing = ref("");
const message = ref("");
const success = ref("");
const adminToken = ref("");
const smtpPassword = ref("");
const resendApiKey = ref("");
const passwordForm = reactive({
  old_password: "",
  new_password: "",
  confirm_password: "",
});

async function load() {
  loading.value = true;
  try {
    settings.value = await api<AppSettingsData>("settings");
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "加载设置失败";
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (!settings.value) return;
  saving.value = true;
  message.value = "";
  success.value = "";
  try {
    settings.value = await api<AppSettingsData>("settings", {
      method: "PATCH",
      body: jsonBody({
        ...settings.value,
        sub2api_admin_token: adminToken.value,
        smtp_password: smtpPassword.value,
        resend_api_key: resendApiKey.value,
      }),
    });
    adminToken.value = "";
    smtpPassword.value = "";
    resendApiKey.value = "";
    success.value = "设置已保存";
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "保存设置失败";
  } finally {
    saving.value = false;
  }
}

async function test(kind: "sub2api" | "email") {
  testing.value = kind;
  message.value = "";
  success.value = "";
  try {
    await api(`settings/test-${kind}`, { method: "POST" });
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
    <button
      class="btn btn-primary btn-sm"
      :disabled="saving || !settings"
      @click="save"
    >
      <span v-if="saving" class="loading loading-xs loading-spinner"></span>
      <AppIcon v-else name="check" class="size-4" />
      保存设置
    </button>
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

  <template v-if="settings">
    <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
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
        <div class="grid gap-3 md:grid-cols-2">
          <fieldset class="fieldset">
            <label class="label">OpenAI 账号 ID</label>
            <input
              v-model.number="settings.openai_account_id"
              type="number"
              min="1"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">平台额度名称</label>
            <input v-model="settings.quota_platform" class="input w-full" />
          </fieldset>
        </div>
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
      </div>
    </section>

    <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
      <div class="card-body">
        <h2 class="card-title">
          <AppIcon name="calculator" class="size-5" />分配模型
        </h2>
        <fieldset class="fieldset">
          <label class="label">成本口径</label>
          <select v-model="settings.cost_basis" class="select w-full">
            <option value="actual">实际扣费（与 Sub2API 平台限额一致）</option>
            <option value="standard">标准计费</option>
          </select>
        </fieldset>
        <div class="grid gap-3 md:grid-cols-2">
          <fieldset class="fieldset">
            <label class="label">无样本时美元 / 1%</label>
            <input
              v-model.number="settings.initial_usd_per_percent"
              type="number"
              min="0"
              step="0.01"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
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
          <fieldset class="fieldset">
            <label class="label">保守分位数</label>
            <input
              v-model.number="settings.conservative_percentile"
              type="number"
              min="1"
              max="50"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">参与计算的历史样本数</label>
            <input
              v-model.number="settings.rate_history_samples"
              type="number"
              min="1"
              max="100"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">建议差额阈值（美元）</label>
            <input
              v-model.number="settings.recommendation_change_usd"
              type="number"
              min="0"
              step="0.01"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">额度耗尽预警余量（美元）</label>
            <input
              v-model.number="settings.limit_warning_usd"
              type="number"
              min="0"
              step="0.01"
              class="input w-full"
            />
          </fieldset>
        </div>
      </div>
    </section>

    <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
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
          空闲时只做低频本地探测；达到成本进度、额度耗尽或重置条件后才形成新观测。
        </p>
      </div>
    </section>

    <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
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
      </div>
    </section>

    <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
      <div class="card-body">
        <h2 class="card-title">
          <AppIcon name="bell-alert" class="size-5" />通知规则
        </h2>
        <label class="label justify-between"
          >参与者额度耗尽时通知<input
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
      </div>
    </section>

    <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
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
  </template>
</template>
