<script setup lang="ts">
import { computed, ref } from "vue";

import SettingLabel from "@/components/common/SettingLabel.vue";
import type { CPAAccountOption, MonitoredAccount } from "@/types/accounts";
import type { AppSettingsData, CPAModelPricing } from "@/types/settings";

import AccountCapacityEditor from "./AccountCapacityEditor.vue";
import CPAModelPricingDialog from "./CPAModelPricingDialog.vue";

const settings = defineModel<AppSettingsData>("settings", { required: true });
const managementKey = defineModel<string>("managementKey", { required: true });
const props = defineProps<{
  accounts: CPAAccountOption[];
  monitoredAccounts: MonitoredAccount[];
  loadingAccounts: boolean;
  testing: boolean;
  saving: boolean;
  savingAccountId: number | "new" | null;
  savePricing: (pricing: CPAModelPricing) => Promise<string | null>;
}>();
const emit = defineEmits<{
  loadAccounts: [];
  test: [];
  save: [];
  saveAccount: [account: MonitoredAccount, create: boolean];
}>();

const pricingDialog = ref<InstanceType<typeof CPAModelPricingDialog> | null>(
  null,
);
const selectedNewAuthIndex = ref<string | null>(null);
const availableAccounts = computed(() => {
  const monitored = new Set(
    props.monitoredAccounts.map((item) => item.cpa_auth_index),
  );
  return props.accounts.filter((item) => !monitored.has(item.auth_index));
});
const profileLabels: Record<
  MonitoredAccount["effective_quota_profile"],
  string
> = {
  plus: "Plus（$100–$200）",
  pro_5x: "Pro 5X（$500–$1,500）",
  pro_20x: "Pro 20X（$1,400–$4,000）",
};
const collectorTone = computed(() => {
  switch (settings.value.cpa_collector_status.state) {
    case "connected":
      return "alert-success";
    case "error":
      return "alert-error";
    case "stale":
      return "alert-warning";
    default:
      return "alert-info";
  }
});
const collectorTitle = computed(() => {
  switch (settings.value.cpa_collector_status.state) {
    case "connected":
      return "Usage 采集器已连接";
    case "error":
      return "Usage 采集器连接失败";
    case "stale":
      return "Usage 采集器心跳过期";
    default:
      return "Usage 采集器等待连接";
  }
});

function formatCollectorTime(value: string | null) {
  if (!value) return "尚无记录";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

function displayedProfile(
  account: MonitoredAccount,
): MonitoredAccount["effective_quota_profile"] {
  if (account.quota_profile !== "auto") return account.quota_profile;
  return account.detected_plan_type === "plus" ? "plus" : "pro_20x";
}

function detectedPlanLabel(account: MonitoredAccount) {
  if (account.detected_plan_type === "plus") return "Plus";
  if (account.detected_plan_type === "pro") return "Pro";
  return "等待下一次额度采样";
}

function resetCapacityRange(account: MonitoredAccount) {
  account.capacity_min_usd_override = null;
  account.capacity_max_usd_override = null;
}

function addSelectedAccount() {
  const option = props.accounts.find(
    (item) => item.auth_index === selectedNewAuthIndex.value,
  );
  if (!option) return;
  emit(
    "saveAccount",
    {
      id: 0,
      provider: "cpa",
      source_account_id: option.auth_index,
      pool_id: 0,
      external_account_id: null,
      cpa_auth_index: option.auth_index,
      name: option.email || option.name || `CPA Codex ${option.auth_index}`,
      enabled: true,
      quota_query_mode: "direct",
      quota_profile: "auto",
      detected_plan_type:
        option.plan_type.toLowerCase() === "plus" ? "plus" : "",
      effective_quota_profile:
        option.plan_type.toLowerCase() === "plus" ? "plus" : "pro_20x",
      capacity_min_usd_override: null,
      capacity_max_usd_override: null,
      capacity_min_usd: option.plan_type.toLowerCase() === "plus" ? 100 : 1400,
      capacity_max_usd: option.plan_type.toLowerCase() === "plus" ? 200 : 4000,
      last_local_check_at: null,
      last_upstream_check_at: null,
      last_success_at: null,
      next_local_check_at: null,
      last_error: "",
    },
    true,
  );
  selectedNewAuthIndex.value = null;
}
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body gap-5">
      <div>
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="card-title">
            <AppIcon name="server" class="size-5" />CPA 连接与账号
          </h2>
          <span class="badge badge-outline badge-sm">CLIProxyAPI</span>
        </div>
        <p class="mt-2 text-sm leading-6 opacity-70">
          从连接后的 CPA 用量事件估算成本并采样 Codex
          七天额度。停用账号只停止额度采样和页面监控， 已纳管账号的原始 usage
          事实仍会继续采集；CPA 账号不参与拼车成员、余额或额度建议。
        </p>
      </div>

      <div
        class="alert items-start text-sm"
        :class="collectorTone"
        data-testid="cpa-collector-status"
      >
        <AppIcon
          :name="
            settings.cpa_collector_status.state === 'connected'
              ? 'check-circle'
              : settings.cpa_collector_status.state === 'idle'
                ? 'information-circle'
                : 'exclamation-triangle'
          "
          class="mt-0.5 size-5 shrink-0"
        />
        <div class="min-w-0 flex-1">
          <div class="font-semibold">{{ collectorTitle }}</div>
          <p v-if="settings.cpa_collector_status.state === 'connected'">
            最近写入：
            {{
              formatCollectorTime(
                settings.cpa_collector_status.last_persisted_at,
              )
            }}
          </p>
          <p v-else-if="settings.cpa_collector_status.state === 'stale'">
            超过 15 秒未收到采集器心跳，当前订阅状态无法确认。
          </p>
          <p
            v-else-if="settings.cpa_collector_status.last_error"
            class="break-words"
          >
            {{ settings.cpa_collector_status.last_error }}
          </p>
          <p v-else>保存有效连接配置并纳管至少一个 CPA 账号后自动连接。</p>
        </div>
        <span
          v-if="settings.cpa_collector_status.pending_count"
          class="badge shrink-0 badge-sm"
        >
          {{ settings.cpa_collector_status.pending_count }} 待写入
        </span>
      </div>

      <div class="grid gap-3">
        <fieldset class="fieldset">
          <label class="label">CPA 地址</label>
          <input
            v-model="settings.cpa_base_url"
            type="url"
            class="input w-full"
          />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label">Management Key</label>
          <input
            v-model="managementKey"
            type="password"
            class="input w-full"
            :placeholder="
              settings.cpa_management_key_configured
                ? '已配置；留空保持不变'
                : '请输入 Management Key'
            "
          />
        </fieldset>

        <div class="grid gap-3 md:grid-cols-2">
          <fieldset class="fieldset">
            <SettingLabel
              label="FAST 倍率"
              help="只对 usage 事件中 service_tier / response_service_tier 标识为 fast 或 priority 的请求生效。普通请求仍按模型基础价格计费。"
            />
            <input
              v-model.number="settings.cpa_fast_multiplier"
              type="number"
              min="1"
              max="100"
              step="0.1"
              class="input w-full"
            />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label">模型价格</label>
            <button
              type="button"
              class="btn justify-start"
              @click="pricingDialog?.open()"
            >
              <AppIcon name="calculator" class="size-4" />编辑
              {{ Object.keys(settings.cpa_model_pricing).length }} 个模型
            </button>
          </fieldset>
        </div>

        <div class="rounded-box border border-base-300 bg-base-100 p-4">
          <label class="label justify-between">
            <span>
              <span class="block font-medium">双倍计费区间</span>
              <span class="mt-1 block text-xs leading-5 opacity-60">
                关闭时长上下文倍率恒为 1；开启后，输入 Token
                严格大于阈值的请求会把整笔请求成本乘以倍率。
              </span>
            </span>
            <input
              v-model="settings.cpa_double_billing_enabled"
              type="checkbox"
              class="toggle toggle-sm"
            />
          </label>
          <div
            v-if="settings.cpa_double_billing_enabled"
            class="mt-3 grid gap-3 md:grid-cols-2"
          >
            <fieldset class="fieldset">
              <label class="label">输入 Token 阈值</label>
              <input
                v-model.number="settings.cpa_double_billing_threshold_tokens"
                type="number"
                min="1"
                step="1000"
                class="input w-full"
              />
            </fieldset>
            <fieldset class="fieldset">
              <label class="label">整笔成本倍率</label>
              <input
                v-model.number="settings.cpa_double_billing_multiplier"
                type="number"
                min="1"
                max="100"
                step="0.1"
                class="input w-full"
              />
            </fieldset>
          </div>
        </div>

        <div class="flex flex-wrap gap-2">
          <button
            class="btn btn-primary btn-sm"
            :disabled="saving"
            @click="emit('save')"
          >
            <span
              v-if="saving"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="check" class="size-4" />保存 CPA 设置
          </button>
          <button class="btn btn-sm" :disabled="testing" @click="emit('test')">
            <span
              v-if="testing"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="signal" class="size-4" />测试连接
          </button>
        </div>
      </div>

      <div class="divider my-0">监控 Codex 账号</div>
      <div class="space-y-3">
        <article
          v-for="account in monitoredAccounts"
          :key="account.id"
          class="rounded-box border border-base-300 bg-base-100 p-4"
        >
          <div
            class="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(12rem,0.8fr)]"
          >
            <fieldset class="fieldset min-w-0">
              <label class="label">本地显示名称</label>
              <input v-model="account.name" class="input w-full" />
            </fieldset>
            <fieldset class="fieldset min-w-0">
              <label class="label">CPA auth_index</label>
              <input
                :value="account.cpa_auth_index"
                class="input w-full font-mono text-xs"
                disabled
              />
            </fieldset>
          </div>
          <fieldset class="mt-2 fieldset">
            <SettingLabel
              label="额度容量档位"
              help="CPA 可识别 Plus / Pro，但无法区分 Pro 5X / 20X；Pro 自动档按 20X，也可手动指定。"
            />
            <select
              v-model="account.quota_profile"
              class="select w-full"
              @change="resetCapacityRange(account)"
            >
              <option value="auto">自动：Plus / Pro（Pro 按 20X）</option>
              <option value="plus">Plus：$100–$200</option>
              <option value="pro_5x">Pro 5X：$500–$1,500</option>
              <option value="pro_20x">Pro 20X：$1,400–$4,000</option>
            </select>
            <p class="mt-1 text-xs leading-5 opacity-60">
              当前按 {{ profileLabels[displayedProfile(account)] }}
              <template v-if="account.quota_profile === 'auto'">
                · CPA：{{ detectedPlanLabel(account) }}
              </template>
            </p>
          </fieldset>
          <AccountCapacityEditor class="mt-3" :account="account" />
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
          尚未添加 CPA 账号。先读取 Codex 账号，再添加需要采样的账号。
        </div>
      </div>

      <fieldset class="fieldset">
        <SettingLabel
          label="添加 CPA Codex 账号"
          help="列表只显示 provider=codex 且有稳定 auth_index 的 CPA 凭据。"
        />
        <div class="join w-full">
          <select
            v-model="selectedNewAuthIndex"
            class="select join-item min-w-0 grow"
          >
            <option :value="null">选择未添加账号</option>
            <option
              v-for="account in availableAccounts"
              :key="account.auth_index"
              :value="account.auth_index"
            >
              {{ account.email || account.name }} ·
              {{ account.plan_type || "未知套餐" }}
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
              selectedNewAuthIndex == null || savingAccountId === 'new'
            "
            @click="addSelectedAccount"
          >
            添加
          </button>
        </div>
      </fieldset>
    </div>
  </section>

  <CPAModelPricingDialog
    ref="pricingDialog"
    :pricing="settings.cpa_model_pricing"
    :save-pricing="props.savePricing"
  />
</template>
