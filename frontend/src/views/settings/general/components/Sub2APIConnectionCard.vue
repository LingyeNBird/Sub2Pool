<script setup lang="ts">
import SettingLabel from "@/components/common/SettingLabel.vue";
import type { AppSettingsData, OpenAIAccountOption } from "@/types";

const settings = defineModel<AppSettingsData>("settings", { required: true });
const adminToken = defineModel<string>("adminToken", { required: true });
const props = defineProps<{
  accounts: OpenAIAccountOption[];
  loadingAccounts: boolean;
  testing: boolean;
  saving: boolean;
}>();
const emit = defineEmits<{
  loadAccounts: [];
  test: [];
  save: [];
}>();

function hasAccountOption(accountId: number | null) {
  return props.accounts.some((account) => account.id === accountId);
}
</script>

<template>
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
        <SettingLabel
          label="OpenAI 上游账号"
          help="使用当前填写的 Sub2API 地址和 Admin Token 临时读取账号列表，无需先保存。选中的账号用于限定本地用量聚合和周限快照。"
        />
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
              v-for="account in accounts"
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
            @click="emit('loadAccounts')"
          >
            <span
              v-if="loadingAccounts"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="arrow-path" class="size-4" />
            读取账号
          </button>
        </div>
      </fieldset>
      <fieldset class="fieldset">
        <SettingLabel
          label="额度百分比查询方式"
          help="被动模式只读取 Sub2API 在真实转发请求中保存的账号快照，不请求 OpenAI；主动模式会通过 Sub2API 调用 OpenAI 官方额度接口。"
        />
        <select v-model="settings.quota_query_mode" class="select w-full">
          <option value="passive">
            被动：仅读取 Sub2API 已保存快照（默认）
          </option>
          <option value="direct">主动：调用上游账号额度接口</option>
        </select>
      </fieldset>
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
          <label class="label">显示与统计时区</label>
          <input
            v-model="settings.timezone"
            class="input w-full"
            placeholder="Asia/Shanghai"
          />
        </fieldset>
      </div>
      <label class="label justify-between"
        >校验 HTTPS 证书<input
          v-model="settings.verify_tls"
          type="checkbox"
          class="toggle toggle-sm"
      /></label>
      <div class="flex flex-wrap gap-2">
        <button class="btn btn-sm" :disabled="testing" @click="emit('test')">
          <span
            v-if="testing"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="signal" class="size-4" />测试连接
        </button>
        <button
          class="btn btn-primary btn-sm"
          :disabled="saving"
          @click="emit('save')"
        >
          <span v-if="saving" class="loading loading-xs loading-spinner"></span>
          <AppIcon v-else name="check" class="size-4" />保存连接设置
        </button>
      </div>
    </div>
  </section>
</template>
