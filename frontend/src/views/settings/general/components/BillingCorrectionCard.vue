<script setup lang="ts">
import { ref, watch } from "vue";
import PricingGroupDialog from "@/components/common/PricingGroupDialog.vue";
import PricingCurrentButton from "./PricingCurrentButton.vue";
import SettingLabel from "@/components/common/SettingLabel.vue";

import { useDateTime } from "@/composables/useDateTime";
import type {
  UpstreamPricingPolicy,
  UpstreamPricingRule,
  UpstreamPricingState,
} from "@/types/settings";

const props = defineProps<{
  state: UpstreamPricingState;
  saving: boolean;
  demo: boolean;
  applyPolicy: (
    policy: UpstreamPricingPolicy,
    groupIds: number[],
  ) => Promise<string | null>;
  revertPolicy: () => Promise<boolean>;
}>();

type RuleKey = "fast_rules" | "model_rules";

const ruleSections: Array<{
  key: RuleKey;
  title: string;
  help: string;
  placeholder: string;
}> = [
  {
    key: "fast_rules",
    title: "FAST 目标倍率",
    help: "按模型匹配写入 FAST 目标倍率。",
    placeholder: "*",
  },
  {
    key: "model_rules",
    title: "模型倍率",
    help: "倍率相对未由本服务加倍的基础价写入，不与已应用值叠乘。",
    placeholder: "gpt-6*",
  },
];

const draft = ref<UpstreamPricingPolicy>({
  fast_rules: [],
  model_rules: [],
  long_context_pricing_enabled: null,
});
const validationMessage = ref("");
const writeDialog = ref<InstanceType<typeof PricingGroupDialog> | null>(null);
const dateTime = useDateTime();

function copyPolicy(policy: UpstreamPricingPolicy): UpstreamPricingPolicy {
  return {
    fast_rules: policy.fast_rules.map((rule) => ({ ...rule })),
    model_rules: policy.model_rules.map((rule) => ({ ...rule })),
    long_context_pricing_enabled: policy.long_context_pricing_enabled,
  };
}

watch(
  () => props.state,
  (state) => {
    draft.value = copyPolicy(state.policy);
    validationMessage.value = "";
  },
  { immediate: true },
);

function addRule(key: RuleKey) {
  if (draft.value[key].length >= 100) return;
  draft.value[key].push({
    model_pattern: "",
    multiplier: key === "fast_rules" ? "2.5" : "1.8",
  });
}

function moveRule(key: RuleKey, index: number, offset: number) {
  const rules = draft.value[key];
  const target = index + offset;
  if (target < 0 || target >= rules.length) return;
  const [rule] = rules.splice(index, 1);
  if (rule) rules.splice(target, 0, rule);
}

function validateRules(rules: UpstreamPricingRule[], label: string): string {
  if (rules.length > 100) return `${label}最多可设置 100 条规则`;
  for (const [index, rule] of rules.entries()) {
    const pattern = rule.model_pattern.trim();
    if (!pattern || pattern.length > 160) {
      return `${label}第 ${index + 1} 条的模型匹配须为 1 至 160 个字符`;
    }
    const multiplier = Number(rule.multiplier);
    if (!Number.isFinite(multiplier) || multiplier < 0.01 || multiplier > 100) {
      return `${label}第 ${index + 1} 条的倍率必须在 0.01 至 100 之间`;
    }
  }
  return "";
}

function normalizedPolicy(): UpstreamPricingPolicy {
  return {
    fast_rules: draft.value.fast_rules.map((rule) => ({
      model_pattern: rule.model_pattern.trim(),
      multiplier: String(rule.multiplier).trim(),
    })),
    model_rules: draft.value.model_rules.map((rule) => ({
      model_pattern: rule.model_pattern.trim(),
      multiplier: String(rule.multiplier).trim(),
    })),
    long_context_pricing_enabled: draft.value.long_context_pricing_enabled,
  };
}

function apply() {
  validationMessage.value =
    validateRules(draft.value.fast_rules, "FAST 规则") ||
    validateRules(draft.value.model_rules, "模型规则");
  if (validationMessage.value) return;
  const policy = normalizedPolicy();
  writeDialog.value?.open(props.state.selected_group_ids, (groupIds) =>
    props.applyPolicy(policy, groupIds),
  );
}

function statusLabel(status: string): string {
  return (
    {
      pending: "等待应用",
      applied: "已应用",
      partial: "部分成功",
      failed: "应用失败",
      reverted: "已撤回",
    }[status] ?? status
  );
}

function statusClass(status: string): string {
  if (status === "applied") return "badge-success";
  if (status === "partial" || status === "pending") return "badge-warning";
  if (status === "failed") return "badge-error";
  return "badge-ghost";
}
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body gap-4">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="card-title">
            <AppIcon name="cloud-arrow-up" class="size-5" />Sub2API 上游计费
          </h2>
          <p class="mt-1 text-sm opacity-65">
            直接写入 Sub2API
            分组计费，影响目标分组中的全部用户。只管理后续上游扣费，不改写或重算历史记录。
          </p>
        </div>
        <span class="badge" :class="statusClass(state.status)">
          {{ statusLabel(state.status) }}
        </span>
      </div>

      <div v-if="demo" class="alert items-start text-sm alert-info">
        <AppIcon name="information-circle" class="mt-0.5 size-5 shrink-0" />
        <span
          >公开演示只更新当前标签页的合成状态，不会向任何 Sub2API
          服务发送请求。</span
        >
      </div>

      <div class="rounded-box bg-base-100 p-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="font-medium">策略版本</span>
          <span class="font-mono text-sm">revision {{ state.revision }}</span>
        </div>
        <dl class="mt-3 grid gap-2 text-xs opacity-65 sm:grid-cols-3">
          <div v-if="state.attempted_at">
            <dt>最近尝试</dt>
            <dd>{{ dateTime(state.attempted_at) }}</dd>
          </div>
          <div v-if="state.applied_at">
            <dt>最近应用</dt>
            <dd>{{ dateTime(state.applied_at) }}</dd>
          </div>
          <div v-if="state.reverted_at">
            <dt>最近撤回</dt>
            <dd>{{ dateTime(state.reverted_at) }}</dd>
          </div>
        </dl>
      </div>

      <div
        v-if="state.last_error"
        class="alert items-start text-sm alert-error"
        role="alert"
      >
        <AppIcon name="exclamation-triangle" class="mt-0.5 size-5 shrink-0" />
        <span>{{ state.last_error }}</span>
      </div>

      <div
        class="overflow-x-auto rounded-box border border-base-300 bg-base-100"
      >
        <table class="table table-sm">
          <thead>
            <tr>
              <th>分组操作记录</th>
              <th>状态</th>
              <th>错误</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="target in state.targets" :key="target.group_id">
              <td>
                <div class="font-medium">{{ target.group_name }}</div>
                <div class="text-xs opacity-50">ID {{ target.group_id }}</div>
              </td>
              <td>
                <span
                  class="badge badge-sm"
                  :class="statusClass(target.status)"
                >
                  {{ statusLabel(target.status) }}
                </span>
              </td>
              <td class="text-sm" :class="{ 'text-error': target.error }">
                {{ target.error || "—" }}
              </td>
            </tr>
            <tr v-if="state.targets.length === 0">
              <td colspan="3" class="py-6 text-center text-sm opacity-60">
                尚无操作记录；点击写入后选择目标分组。
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <fieldset :disabled="saving" class="space-y-4">
        <section
          v-for="section in ruleSections"
          :key="section.key"
          class="rounded-box border border-base-300 bg-base-100 p-4"
        >
          <div class="flex flex-wrap items-center gap-2">
            <h3 class="font-semibold">
              <SettingLabel
                :label="section.title"
                :help="section.help"
                class="p-0 text-inherit"
              />
            </h3>
            <PricingCurrentButton
              :kind="section.key === 'fast_rules' ? 'fast' : 'model'"
              :demo="demo"
            />
            <button
              type="button"
              class="btn ml-auto shrink-0 btn-outline btn-xs"
              :disabled="draft[section.key].length >= 100"
              @click="addRule(section.key)"
            >
              <AppIcon name="plus" class="size-3.5" />添加规则
            </button>
          </div>

          <div
            v-for="(rule, index) in draft[section.key]"
            :key="index"
            class="mt-3 grid grid-cols-[minmax(0,1fr)_auto] items-end gap-2 sm:grid-cols-[minmax(0,1fr)_7rem_auto]"
          >
            <label class="col-span-2 fieldset min-w-0 gap-1 p-0 sm:col-span-1">
              <span class="fieldset-legend p-0 text-xs">模型匹配</span>
              <input
                v-model="rule.model_pattern"
                class="input w-full font-mono input-sm"
                type="text"
                maxlength="160"
                :placeholder="section.placeholder"
              />
            </label>
            <label class="fieldset min-w-0 gap-1 p-0">
              <span class="fieldset-legend p-0 text-xs">倍率</span>
              <input
                v-model="rule.multiplier"
                class="input w-full input-sm"
                type="number"
                min="0.01"
                max="100"
                step="0.01"
                inputmode="decimal"
              />
            </label>
            <div class="flex items-center gap-0.5 pb-0.5">
              <button
                type="button"
                class="btn btn-square btn-ghost btn-xs"
                :disabled="index === 0"
                aria-label="上移规则"
                title="上移规则"
                @click="moveRule(section.key, index, -1)"
              >
                <AppIcon name="chevron-up" class="size-3.5" />
              </button>
              <button
                type="button"
                class="btn btn-square btn-ghost btn-xs"
                :disabled="index === draft[section.key].length - 1"
                aria-label="下移规则"
                title="下移规则"
                @click="moveRule(section.key, index, 1)"
              >
                <AppIcon name="chevron-up" class="size-3.5 rotate-180" />
              </button>
              <button
                type="button"
                class="btn btn-ghost px-1.5 text-error btn-xs"
                aria-label="删除规则"
                title="删除规则"
                @click="draft[section.key].splice(index, 1)"
              >
                删除
              </button>
            </div>
          </div>
          <p
            v-if="draft[section.key].length === 0"
            class="mt-3 text-xs opacity-60"
          >
            空规则表示不接管该项；应用时恢复接管前的上游值。
          </p>
        </section>

        <section class="rounded-box border border-base-300 bg-base-100 p-4">
          <div class="flex items-center gap-2">
            <label for="upstream-context-policy" class="font-semibold"
              >长上下文阶梯计费</label
            >
            <PricingCurrentButton kind="context" :demo="demo" />
          </div>
          <select
            id="upstream-context-policy"
            v-model="draft.long_context_pricing_enabled"
            class="select mt-3 w-full select-sm"
          >
            <option :value="null">不接管（恢复接管前的上游值）</option>
            <option :value="true">启用</option>
            <option :value="false">关闭</option>
          </select>
        </section>
      </fieldset>

      <div
        v-if="validationMessage"
        class="alert text-sm alert-error"
        role="alert"
      >
        <span>{{ validationMessage }}</span>
      </div>

      <p class="text-xs opacity-60">
        规则按列表顺序提交。清空某类规则或把长上下文设为“不接管”，会恢复该项接管前保存的
        Sub2API
        值，不会与已应用值叠乘。仅覆盖本次能够解析的模型；上游新增模型后请重新应用。
      </p>
      <p class="text-xs opacity-65">
        复杂渠道定价或无法无损展开的已有通配符价卡会明确报错，不会静默覆盖。
      </p>

      <div class="flex flex-wrap justify-end gap-2">
        <button
          type="button"
          class="btn btn-outline btn-sm"
          :disabled="saving || !state.can_revert"
          @click="revertPolicy"
        >
          <AppIcon name="arrow-uturn-left" class="size-4" />
          撤回至接管前值
        </button>
        <button
          type="button"
          class="btn btn-primary btn-sm"
          :disabled="saving"
          @click="apply"
        >
          <span v-if="saving" class="loading loading-xs loading-spinner"></span>
          <AppIcon v-else name="cloud-arrow-up" class="size-4" />
          写入 Sub2API 分组计费
        </button>
      </div>
    </div>
  </section>
  <PricingGroupDialog ref="writeDialog" />
</template>
