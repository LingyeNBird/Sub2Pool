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
const profileLabels: Record<
  MonitoredAccount["effective_quota_profile"],
  string
> = {
  plus: "Plus（$100–$200）",
  pro_5x: "Pro 5X（$500–$1,500）",
  pro_20x: "Pro 20X（$1,400–$4,000）",
};
const profileCapacityDefaults: Record<
  MonitoredAccount["effective_quota_profile"],
  {
    min: number;
    max: number;
    sliderMin: number;
    sliderMax: number;
    step: number;
  }
> = {
  plus: { min: 100, max: 200, sliderMin: 10, sliderMax: 1000, step: 10 },
  pro_5x: {
    min: 500,
    max: 1500,
    sliderMin: 100,
    sliderMax: 3000,
    step: 50,
  },
  pro_20x: {
    min: 1400,
    max: 4000,
    sliderMin: 500,
    sliderMax: 6000,
    step: 100,
  },
};

function detectedPlanLabel(account: MonitoredAccount) {
  if (account.detected_plan_type === "plus") return "Plus";
  if (account.detected_plan_type === "pro") return "Pro";
  return account.quota_query_mode === "direct"
    ? "等待下一次主动额度采样"
    : "被动模式无法识别";
}
function displayedProfile(
  account: MonitoredAccount,
): MonitoredAccount["effective_quota_profile"] {
  if (account.quota_profile !== "auto") return account.quota_profile;
  return account.detected_plan_type === "plus" ? "plus" : "pro_20x";
}
function capacityDefaults(account: MonitoredAccount) {
  return profileCapacityDefaults[displayedProfile(account)];
}

function capacityMin(account: MonitoredAccount) {
  return account.capacity_min_usd_override ?? capacityDefaults(account).min;
}

function capacityMax(account: MonitoredAccount) {
  return account.capacity_max_usd_override ?? capacityDefaults(account).max;
}

function capacityDomain(account: MonitoredAccount) {
  const defaults = capacityDefaults(account);
  const currentMin = capacityMin(account);
  const currentMax = capacityMax(account);
  const min = Math.min(
    defaults.sliderMin,
    Math.floor(currentMin / defaults.step) * defaults.step,
  );
  const max = Math.max(
    defaults.sliderMax,
    Math.ceil(currentMax / defaults.step) * defaults.step,
  );
  const gap = currentMax - currentMin;
  const coarseStep = Math.min(
    defaults.step,
    Math.max(0.01, Math.floor((gap / 2) * 100) / 100),
  );
  const aligned = [currentMin, currentMax].every((value) => {
    const offset = (value - min) / coarseStep;
    return Math.abs(offset - Math.round(offset)) < 1e-8;
  });
  return { min, max, step: aligned ? coarseStep : 0.01 };
}

function capacityRangeStyle(account: MonitoredAccount) {
  const domain = capacityDomain(account);
  const span = domain.max - domain.min;
  return {
    "--range-start": `${((capacityMin(account) - domain.min) / span) * 100}%`,
    "--range-end": `${((capacityMax(account) - domain.min) / span) * 100}%`,
  };
}

function updateCapacityBoundary(
  account: MonitoredAccount,
  boundary: "min" | "max",
  event: Event,
  minimumGap = 0.01,
) {
  const parsed = Number((event.target as HTMLInputElement).value);
  if (!Number.isFinite(parsed)) return;
  const value = Math.round(parsed * 100) / 100;
  const currentMin = capacityMin(account);
  const currentMax = capacityMax(account);
  account.capacity_min_usd_override =
    boundary === "min"
      ? Math.max(1, Math.min(value, currentMax - minimumGap))
      : currentMin;
  account.capacity_max_usd_override =
    boundary === "max"
      ? Math.min(50000, Math.max(value, currentMin + minimumGap))
      : currentMax;
}

function resetCapacityRange(account: MonitoredAccount) {
  account.capacity_min_usd_override = null;
  account.capacity_max_usd_override = null;
}

function formatCapacity(value: number) {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

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
      quota_profile: "auto",
      detected_plan_type: "",
      effective_quota_profile: "pro_20x",
      capacity_min_usd_override: null,
      capacity_max_usd_override: null,
      capacity_min_usd: 1400,
      capacity_max_usd: 4000,
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
          <div class="mt-2 grid gap-3 md:grid-cols-2">
            <fieldset class="fieldset">
              <SettingLabel
                label="额度百分比查询方式"
                help="被动模式读取 Sub2API 已保存快照；主动模式通过 Sub2API 调用 OpenAI 官方额度接口。"
              />
              <select v-model="account.quota_query_mode" class="select w-full">
                <option value="passive">被动：读取 Sub2API 快照</option>
                <option value="direct">主动：调用上游额度接口</option>
              </select>
            </fieldset>
            <fieldset class="fieldset">
              <SettingLabel
                label="额度容量档位"
                help="主动额度采样可自动识别 Plus 或 Pro；OpenAI 不提供 Pro 5X/20X 区分，被动模式也不提供套餐，因此这些情况可手动指定。"
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
                当前按
                {{ profileLabels[displayedProfile(account)] }}
                <template v-if="account.quota_profile === 'auto'">
                  · 上游：{{ detectedPlanLabel(account) }}
                </template>
              </p>
            </fieldset>
          </div>
          <div
            class="mt-3 rounded-box border border-base-300 bg-base-200/45 px-4 py-3"
          >
            <div class="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div class="text-sm font-medium">基础容量范围</div>
                <p class="mt-0.5 text-xs leading-5 opacity-60">
                  粒子滤波每个新周期从这里开始；越界证据仍可触发上下扩张。
                </p>
              </div>
              <button
                v-if="account.capacity_min_usd_override != null"
                type="button"
                class="btn btn-ghost btn-xs"
                @click="resetCapacityRange(account)"
              >
                恢复档位默认值
              </button>
            </div>
            <div
              class="capacity-range mt-3"
              :style="capacityRangeStyle(account)"
            >
              <div class="capacity-range__track">
                <span class="capacity-range__active"></span>
              </div>
              <input
                type="range"
                aria-label="基础容量下限"
                :min="capacityDomain(account).min"
                :max="capacityDomain(account).max"
                :step="capacityDomain(account).step"
                :value="capacityMin(account)"
                @input="
                  updateCapacityBoundary(
                    account,
                    'min',
                    $event,
                    capacityDomain(account).step,
                  )
                "
              />
              <input
                type="range"
                aria-label="基础容量上限"
                :min="capacityDomain(account).min"
                :max="capacityDomain(account).max"
                :step="capacityDomain(account).step"
                :value="capacityMax(account)"
                @input="
                  updateCapacityBoundary(
                    account,
                    'max',
                    $event,
                    capacityDomain(account).step,
                  )
                "
              />
            </div>
            <div class="mt-2 grid grid-cols-2 gap-3">
              <fieldset class="fieldset min-w-0">
                <label class="label text-xs">下限（美元）</label>
                <label class="input w-full input-sm">
                  <span class="opacity-50">$</span>
                  <input
                    type="number"
                    aria-label="基础容量下限（美元）"
                    min="1"
                    max="49999.99"
                    step="0.01"
                    :value="capacityMin(account)"
                    @change="updateCapacityBoundary(account, 'min', $event)"
                  />
                </label>
              </fieldset>
              <fieldset class="fieldset min-w-0">
                <label class="label text-xs">上限（美元）</label>
                <label class="input w-full input-sm">
                  <span class="opacity-50">$</span>
                  <input
                    type="number"
                    aria-label="基础容量上限（美元）"
                    min="1.01"
                    max="50000"
                    step="0.01"
                    :value="capacityMax(account)"
                    @change="updateCapacityBoundary(account, 'max', $event)"
                  />
                </label>
              </fieldset>
            </div>
            <p class="mt-1 text-xs opacity-60">
              当前基础范围：${{ formatCapacity(capacityMin(account)) }} – ${{
                formatCapacity(capacityMax(account))
              }}
              <span v-if="account.capacity_min_usd_override != null">
                · 自定义
              </span>
            </p>
          </div>
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

<style scoped>
.capacity-range {
  position: relative;
  height: 1.75rem;
}

.capacity-range__track {
  position: absolute;
  top: 50%;
  right: 0.5rem;
  left: 0.5rem;
  height: 0.3rem;
  overflow: hidden;
  border-radius: 999px;
  background: var(--color-base-300);
  transform: translateY(-50%);
}

.capacity-range__active {
  position: absolute;
  top: 0;
  right: calc(100% - var(--range-end));
  bottom: 0;
  left: var(--range-start);
  border-radius: inherit;
  background: var(--color-primary);
}

.capacity-range input[type="range"] {
  position: absolute;
  inset: 0;
  z-index: 1;
  width: 100%;
  height: 1.75rem;
  margin: 0;
  appearance: none;
  background: transparent;
  pointer-events: none;
}

.capacity-range input[type="range"]:last-child {
  z-index: 2;
}

.capacity-range input[type="range"]::-webkit-slider-runnable-track {
  height: 0.3rem;
  background: transparent;
}

.capacity-range input[type="range"]::-webkit-slider-thumb {
  width: 1rem;
  height: 1rem;
  margin-top: -0.35rem;
  appearance: none;
  border: 2px solid var(--color-primary);
  border-radius: 999px;
  background: var(--color-base-100);
  box-shadow: 0 1px 3px
    color-mix(in oklab, var(--color-base-content) 20%, transparent);
  cursor: grab;
  pointer-events: auto;
}

.capacity-range input[type="range"]::-moz-range-track {
  height: 0.3rem;
  background: transparent;
}

.capacity-range input[type="range"]::-moz-range-thumb {
  width: 0.875rem;
  height: 0.875rem;
  border: 2px solid var(--color-primary);
  border-radius: 999px;
  background: var(--color-base-100);
  box-shadow: 0 1px 3px
    color-mix(in oklab, var(--color-base-content) 20%, transparent);
  cursor: grab;
  pointer-events: auto;
}

.capacity-range input[type="range"]:focus-visible::-webkit-slider-thumb {
  outline: 2px solid color-mix(in oklab, var(--color-primary) 35%, transparent);
  outline-offset: 3px;
}
</style>
