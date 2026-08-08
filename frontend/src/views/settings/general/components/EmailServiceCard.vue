<script setup lang="ts">
import SettingLabel from "@/components/common/SettingLabel.vue";
import type { AppSettingsData } from "@/types";

const settings = defineModel<AppSettingsData>("settings", { required: true });
const smtpPassword = defineModel<string>("smtpPassword", { required: true });
const resendApiKey = defineModel<string>("resendApiKey", { required: true });
defineProps<{ testing: boolean; saving: boolean }>();
const emit = defineEmits<{ test: []; save: [] }>();
</script>

<template>
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
            @change="settings.smtp_use_tls && (settings.smtp_use_ssl = false)"
        /></label>
        <label class="label justify-between"
          >直接 SSL<input
            v-model="settings.smtp_use_ssl"
            type="checkbox"
            class="toggle toggle-sm"
            @change="settings.smtp_use_ssl && (settings.smtp_use_tls = false)"
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
          <SettingLabel
            label="Resend 发件人"
            help="支持“名称 <邮箱>”格式；正式发送前必须在 Resend 中验证对应域名。"
          />
          <input
            v-model="settings.resend_from_email"
            class="input w-full"
            placeholder="拼车额度 &lt;notice@example.com&gt;"
          />
        </fieldset>
      </template>

      <div class="flex flex-wrap gap-2">
        <button class="btn btn-sm" :disabled="testing" @click="emit('test')">
          <span
            v-if="testing"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="paper-airplane" class="size-4" />发送测试邮件
        </button>
        <button
          class="btn btn-primary btn-sm"
          :disabled="saving"
          @click="emit('save')"
        >
          <span v-if="saving" class="loading loading-xs loading-spinner"></span>
          <AppIcon v-else name="check" class="size-4" />保存邮件设置
        </button>
      </div>
    </div>
  </section>
</template>
