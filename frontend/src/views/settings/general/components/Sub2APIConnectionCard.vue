<script setup lang="ts">
import { computed, ref } from "vue";

import SettingLabel from "@/components/common/SettingLabel.vue";
import type { MonitoredAccount, OpenAIAccountOption } from "@/types/accounts";
import type { AppSettingsData } from "@/types/settings";

const settings = defineModel<AppSettingsData>("settings", { required: true });
const adminToken = defineModel<string>("adminToken", { required: true });
const selectedTestAccountId = defineModel<number | null>(
  "selectedTestAccountId",
  {
    required: true,
  },
);
const props = defineProps<{
  accounts: OpenAIAccountOption[];
  monitoredAccounts: MonitoredAccount[];
  loadingAccounts: boolean;
  testing: boolean;
  saving: boolean;
  savingAccountId: number | "new" | null;
}>();
const emit = defineEmits<{
  loadAccounts: [];
  test: [];
  save: [];
  saveAccount: [account: MonitoredAccount, create: boolean];
  removeAccount: [account: MonitoredAccount];
}>();

const selectedNewAccountId = ref<number | null>(null);
const availableAccounts = computed(() => {
  const monitored = new Set(
    props.monitoredAccounts.map((item) => item.external_account_id),
  );
  return props.accounts.filter((item) => !monitored.has(item.id));
});

function addSelectedAccount() {
  const option = props.accounts.find(
    (item) => item.id === selectedNewAccountId.value,
  );
  if (!option) return;
  emit(
    "saveAccount",
    {
      id: 0,
      pool_id: 0,
      external_account_id: option.id,
      name: option.name || `OpenAI 账号 ${option.id}`,
      enabled: true,
      quota_query_mode: "passive",
      last_local_check_at: null,
      last_upstream_check_at: null,
      last_success_at: null,
      next_local_check_at: null,
      last_error: "",
    },
    true,
  );
  selectedNewAccountId.value = null;
}
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body gap-5">
      <div>
        <h2 class="card-title">
          <AppIcon name="code-bracket" class="size-5" />Sub2API 连接与账号
        </h2>
        <p class="mt-2 text-sm leading-6 opacity-70">
          一个 Sub2API 管理连接可监控多个 OpenAI
          上游账号。每个账号独立采样、重放和分配，参与者余额按账号建议合计。
        </p>
      </div>

      <div class="grid gap-3">
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
        <label class="label justify-between">
          校验 HTTPS 证书
          <input
            v-model="settings.verify_tls"
            type="checkbox"
            class="toggle toggle-sm"
          />
        </label>
        <button
          class="btn justify-self-start btn-primary btn-sm"
          :disabled="saving"
          @click="emit('save')"
        >
          <span v-if="saving" class="loading loading-xs loading-spinner"></span>
          <AppIcon v-else name="check" class="size-4" />保存连接设置
        </button>
      </div>

      <div class="divider my-0">监控账号</div>
      <div class="space-y-3">
        <article
          v-for="account in monitoredAccounts"
          :key="account.id"
          class="rounded-box border border-base-300 bg-base-100 p-4"
        >
          <div class="grid gap-3 md:grid-cols-[minmax(0,1fr)_10rem]">
            <fieldset class="fieldset min-w-0">
              <label class="label">显示名称</label>
              <input v-model="account.name" class="input w-full" />
            </fieldset>
            <fieldset class="fieldset">
              <label class="label">上游 ID</label>
              <input
                :value="account.external_account_id"
                class="input w-full font-mono"
                disabled
              />
            </fieldset>
          </div>
          <fieldset class="mt-2 fieldset">
            <SettingLabel
              label="额度百分比查询方式"
              help="被动模式读取 Sub2API 已保存快照；主动模式通过 Sub2API 调用 OpenAI 官方额度接口。"
            />
            <select v-model="account.quota_query_mode" class="select w-full">
              <option value="passive">被动：读取 Sub2API 快照</option>
              <option value="direct">主动：调用上游额度接口</option>
            </select>
          </fieldset>
          <div class="mt-3 flex flex-wrap items-center justify-between gap-3">
            <label class="label gap-3">
              <input
                v-model="account.enabled"
                type="checkbox"
                class="toggle toggle-sm"
              />
              启用采样
            </label>
            <div class="flex gap-2">
              <button
                class="btn btn-ghost btn-sm"
                :disabled="savingAccountId === account.id"
                @click="emit('removeAccount', account)"
              >
                删除
              </button>
              <button
                class="btn btn-sm"
                :disabled="savingAccountId === account.id"
                @click="emit('saveAccount', account, false)"
              >
                <span
                  v-if="savingAccountId === account.id"
                  class="loading loading-xs loading-spinner"
                ></span>
                保存账号
              </button>
            </div>
          </div>
          <p v-if="account.last_error" class="mt-2 text-xs text-error">
            {{ account.last_error }}
          </p>
        </article>
        <div
          v-if="!monitoredAccounts.length"
          class="rounded-box border border-dashed border-base-300 p-4 text-sm opacity-60"
        >
          尚未添加监控账号。先读取 Sub2API 账号列表，再添加需要独立测算的账号。
        </div>
      </div>

      <fieldset class="fieldset">
        <SettingLabel
          label="添加 OpenAI 上游账号"
          help="账号只能添加一次；添加后可独立设置查询方式和启用状态。"
        />
        <div class="join w-full">
          <select
            v-model.number="selectedNewAccountId"
            class="select join-item grow"
          >
            <option :value="null">选择未添加账号</option>
            <option
              v-for="account in availableAccounts"
              :key="account.id"
              :value="account.id"
            >
              {{ account.name }}（ID {{ account.id }} ·
              {{ account.status || "未知状态" }}）
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
            <AppIcon v-else name="arrow-path" class="size-4" />读取
          </button>
          <button
            class="btn join-item btn-primary"
            :disabled="
              selectedNewAccountId == null || savingAccountId === 'new'
            "
            @click="addSelectedAccount"
          >
            添加
          </button>
        </div>
      </fieldset>

      <fieldset class="fieldset">
        <label class="label">连接测试账号</label>
        <div class="join w-full">
          <select
            v-model.number="selectedTestAccountId"
            class="select join-item grow"
          >
            <option :value="null">仅测试管理连接</option>
            <option
              v-for="account in monitoredAccounts"
              :key="account.id"
              :value="account.id"
            >
              {{ account.name }}（{{
                account.quota_query_mode === "direct" ? "主动" : "被动"
              }}）
            </option>
          </select>
          <button
            class="btn join-item"
            :disabled="testing"
            @click="emit('test')"
          >
            <span
              v-if="testing"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="signal" class="size-4" />测试连接
          </button>
        </div>
      </fieldset>
    </div>
  </section>
</template>
