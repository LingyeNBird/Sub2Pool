<script setup lang="ts">
import { computed, ref } from "vue";
import type { AppSettingsData } from "@/types/settings";

const settings = defineModel<AppSettingsData>("settings", { required: true });
const props = defineProps<{ saving: boolean; save: () => Promise<boolean> }>();
type Kind = "fast" | "long" | "model";
interface DraftRule {
  model_pattern: string;
  source_multiplier: string | number;
  target_multiplier: string | number;
  multiplier: string | number;
  threshold_tokens: string | number;
}
const sections = [
  {
    kind: "fast",
    title: "FAST 模型修正",
    enabled: "fast_correction_enabled",
    rules: "fast_correction_rules",
  },
  {
    kind: "long",
    title: "双倍倍率修正",
    enabled: "long_context_correction_enabled",
    rules: "long_context_correction_rules",
  },
  {
    kind: "model",
    title: "模型计费倍率",
    enabled: "model_correction_enabled",
    rules: "model_correction_rules",
  },
] as const;
const active = ref<Kind>("fast");
const section = computed(() =>
  sections.find((item) => item.kind === active.value)!,
);
const dialog = ref<HTMLDialogElement | null>(null);
const draftRules = ref<DraftRule[]>([]);
const validationMessage = ref("");

function newRule(): DraftRule {
  return {
    model_pattern: "",
    source_multiplier: "2",
    target_multiplier: active.value === "fast" ? "2.5" : "1",
    multiplier: "1.8",
    threshold_tokens: 272000,
  };
}
function openRules(kind: Kind) {
  active.value = kind;
  draftRules.value = settings.value[section.value.rules].map((rule) => ({
    ...newRule(),
    ...rule,
  }));
  validationMessage.value = "";
  dialog.value?.showModal();
}
function closeRules() {
  if (!props.saving) dialog.value?.close();
}
function onCancel(event: Event) {
  if (props.saving) event.preventDefault();
}
function addRule() {
  if (draftRules.value.length >= 100) return;
  const catchAll = draftRules.value.findIndex(
    (rule) => rule.model_pattern.trim() === "*",
  );
  draftRules.value.splice(
    catchAll < 0 ? draftRules.value.length : catchAll,
    0,
    newRule(),
  );
}
function moveRule(index: number, offset: number) {
  const target = index + offset;
  if (target < 0 || target >= draftRules.value.length) return;
  const [rule] = draftRules.value.splice(index, 1);
  if (rule) draftRules.value.splice(target, 0, rule);
}
function validateRules(): string {
  if (draftRules.value.length > 100) return "最多可设置 100 条规则";
  for (const [index, rule] of draftRules.value.entries()) {
    if (!rule.model_pattern.trim() || rule.model_pattern.trim().length > 160)
      return `第 ${index + 1} 条规则的模型匹配须为 1 至 160 个字符`;
    const values =
      active.value === "model"
        ? [rule.multiplier]
        : [rule.source_multiplier, rule.target_multiplier];
    if (
      values.some(
        (value) =>
          !Number.isFinite(Number(value)) ||
          Number(value) < 0.01 ||
          Number(value) > 100,
      )
    )
      return `第 ${index + 1} 条规则的倍率必须在 0.01 至 100 之间`;
    if (
      active.value === "fast" &&
      Number(rule.target_multiplier) < Number(rule.source_multiplier)
    )
      return `第 ${index + 1} 条 FAST 规则的目标倍率不能小于源倍率`;
    const threshold = Number(rule.threshold_tokens);
    if (
      active.value === "long" &&
      (!Number.isInteger(threshold) || threshold < 1 || threshold > 100000000)
    )
      return `第 ${index + 1} 条规则的阈值必须为 1 至 100000000 的整数`;
  }
  return "";
}
async function saveRules() {
  validationMessage.value = validateRules();
  if (validationMessage.value) return;
  const previousFast = settings.value.fast_correction_rules;
  const previousLong = settings.value.long_context_correction_rules;
  const previousModel = settings.value.model_correction_rules;
  if (active.value === "fast")
    settings.value.fast_correction_rules = draftRules.value.map(
      ({ model_pattern, source_multiplier, target_multiplier }) => ({
        model_pattern: model_pattern.trim(),
        source_multiplier,
        target_multiplier,
      }),
    );
  else if (active.value === "long")
    settings.value.long_context_correction_rules = draftRules.value.map(
      ({
        model_pattern,
        source_multiplier,
        target_multiplier,
        threshold_tokens,
      }) => ({
        model_pattern: model_pattern.trim(),
        source_multiplier,
        target_multiplier,
        threshold_tokens: Number(threshold_tokens),
      }),
    );
  else
    settings.value.model_correction_rules = draftRules.value.map(
      ({ model_pattern, multiplier }) => ({
        model_pattern: model_pattern.trim(),
        multiplier,
      }),
    );
  if (await props.save()) dialog.value?.close();
  else {
    settings.value.fast_correction_rules = previousFast;
    settings.value.long_context_correction_rules = previousLong;
    settings.value.model_correction_rules = previousModel;
    validationMessage.value =
      "保存失败，规则尚未生效。请检查页面错误提示后重试。";
  }
}
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body gap-4">
      <h2 class="card-title">
        <AppIcon name="adjustments-horizontal" class="size-5" />计费修正
      </h2>
      <p class="text-sm opacity-65">
        三种修正统一管理，仅调整 Sub2Pool 测算成本，不修改
        Sub2API。保存后使用本地原始事实重算。
      </p>
      <div
        v-for="item in sections"
        :key="item.kind"
        class="flex flex-wrap items-center justify-between gap-3 rounded-box bg-base-100 p-3"
      >
        <label class="flex cursor-pointer items-center gap-3">
          <input
            v-model="settings[item.enabled]"
            type="checkbox"
            class="toggle toggle-sm"
            :disabled="saving"
          />
          <span class="font-medium">{{ item.title }}</span>
        </label>
        <button
          type="button"
          class="btn btn-outline btn-sm"
          :disabled="saving"
          @click="openRules(item.kind)"
        >
          配置规则 · {{ settings[item.rules].length }}
        </button>
      </div>
      <div
        v-if="settings.correction_missing_intervals"
        class="alert items-start text-sm alert-warning"
      >
        <AppIcon name="exclamation-triangle" class="mt-0.5 size-5 shrink-0" />
        <span
          >当前周期
          {{ settings.correction_missing_intervals }} 个区间缺少原始请求事实。旧
          FAST
          修正保留，无法证实的新修正不补造；完整事实区间可直接重算，无需重新请求上游。</span
        >
      </div>
      <button
        type="button"
        class="btn btn-primary btn-sm"
        :disabled="saving"
        @click="save"
      >
        <span v-if="saving" class="loading loading-xs loading-spinner"></span>
        <AppIcon v-else name="check" class="size-4" />保存修正设置
      </button>
    </div>
  </section>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="modal"
      aria-labelledby="billing-rule-title"
      @cancel="onCancel"
    >
      <div
        class="modal-box flex max-h-[calc(100dvh-2rem)] max-w-4xl flex-col overflow-hidden p-0"
      >
        <header
          class="flex shrink-0 items-center justify-between gap-4 border-b border-base-300 px-5 py-4"
        >
          <h2 id="billing-rule-title" class="text-lg font-bold">
            {{ section.title }}规则
          </h2>
          <button
            type="button"
            class="btn btn-circle btn-ghost btn-sm"
            aria-label="关闭修正规则"
            :disabled="saving"
            @click="closeRules"
          >
            <AppIcon name="x-mark" class="size-4" />
          </button>
        </header>
        <div class="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:p-6">
          <div class="alert items-start text-sm alert-info">
            <span
              >支持 *
              通配符，不区分大小写，从上到下命中第一条后停止；不同修正类型按
              FAST → 长上下文 → 模型倍率叠加。</span
            >
          </div>
          <p v-if="active === 'long'" class="text-sm opacity-70">
            默认匹配 GPT-5.6 与 GPT-6 系列，将 Sub2API 双倍倍率 2 修正为
            1。上游已设为 1 时，将源倍率也改为
            1。优先使用上游实际计费标记；缺少标记时，以普通输入、缓存写入与缓存命中
            Token 之和严格大于兜底阈值判断，不包含输出 Token。
          </p>
          <p v-else-if="active === 'model'" class="text-sm opacity-70">
            默认将 GPT-6 系列成本乘以 1.8。上游已经应用此倍率时请改为
            1，避免重复修正。
          </p>
          <p v-else class="text-sm opacity-70">
            仅修正 FAST 请求，默认由 Sub2API 2 倍换算为 2.5
            倍。上游已正确配置时，将源倍率与目标倍率设为相同值。
          </p>
          <fieldset
            v-for="(rule, index) in draftRules"
            :key="index"
            class="fieldset rounded-box border border-base-300 bg-base-200 p-4"
            :disabled="saving"
          >
            <legend class="fieldset-legend">规则 {{ index + 1 }}</legend>
            <div class="mb-2 flex justify-end gap-1">
              <button
                type="button"
                class="btn btn-ghost btn-xs"
                :disabled="index === 0"
                aria-label="上移规则"
                @click="moveRule(index, -1)"
              >
                ↑
              </button>
              <button
                type="button"
                class="btn btn-ghost btn-xs"
                :disabled="index === draftRules.length - 1"
                aria-label="下移规则"
                @click="moveRule(index, 1)"
              >
                ↓
              </button>
              <button
                type="button"
                class="btn btn-ghost text-error btn-xs"
                aria-label="删除规则"
                @click="draftRules.splice(index, 1)"
              >
                删除
              </button>
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <label class="fieldset"
                ><span class="fieldset-legend">模型匹配</span
                ><input
                  v-model.trim="rule.model_pattern"
                  type="text"
                  class="input w-full font-mono"
                  maxlength="160"
                  placeholder="gpt-6*"
              /></label>
              <label v-if="active === 'model'" class="fieldset"
                ><span class="fieldset-legend">计费倍率</span
                ><input
                  v-model="rule.multiplier"
                  type="number"
                  class="input w-full"
                  min="0.01"
                  max="100"
                  step="0.01"
                  inputmode="decimal"
              /></label>
              <template v-else>
                <label class="fieldset"
                  ><span class="fieldset-legend"
                    >Sub2API {{ active === "long" ? "双倍" : "FAST" }}倍率</span
                  ><input
                    v-model="rule.source_multiplier"
                    type="number"
                    class="input w-full"
                    min="0.01"
                    max="100"
                    step="0.01"
                    inputmode="decimal"
                /></label>
                <label class="fieldset"
                  ><span class="fieldset-legend">修正目标倍率</span
                  ><input
                    v-model="rule.target_multiplier"
                    type="number"
                    class="input w-full"
                    min="0.01"
                    max="100"
                    step="0.01"
                    inputmode="decimal"
                /></label>
                <label v-if="active === 'long'" class="fieldset"
                  ><span class="fieldset-legend">兜底阈值（输入 Token）</span
                  ><input
                    v-model="rule.threshold_tokens"
                    type="number"
                    class="input w-full"
                    min="1"
                    max="100000000"
                    step="1"
                    inputmode="numeric"
                /></label>
              </template>
            </div>
          </fieldset>
          <p
            v-if="draftRules.length === 0"
            class="py-6 text-center text-sm opacity-60"
          >
            没有规则时，该项修正对所有模型均不生效。
          </p>
          <button
            type="button"
            class="btn btn-outline btn-sm"
            :disabled="saving || draftRules.length >= 100"
            @click="addRule"
          >
            <AppIcon name="plus" class="size-4" />添加模型规则
          </button>
          <div
            v-if="validationMessage"
            class="alert text-sm alert-error"
            role="alert"
          >
            <span>{{ validationMessage }}</span>
          </div>
        </div>
        <footer
          class="flex shrink-0 justify-end gap-2 border-t border-base-300 px-5 py-4"
        >
          <button
            type="button"
            class="btn btn-ghost btn-sm"
            :disabled="saving"
            @click="closeRules"
          >
            取消
          </button>
          <button
            type="button"
            class="btn btn-primary btn-sm"
            :disabled="saving"
            @click="saveRules"
          >
            <span
              v-if="saving"
              class="loading loading-xs loading-spinner"
            ></span
            >保存规则并重算
          </button>
        </footer>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button :disabled="saving">关闭</button>
      </form>
    </dialog>
  </Teleport>
</template>
