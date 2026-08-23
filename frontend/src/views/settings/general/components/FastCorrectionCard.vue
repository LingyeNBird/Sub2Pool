<script setup lang="ts">
import { ref } from "vue";

import SettingLabel from "@/components/common/SettingLabel.vue";
import type { AppSettingsData, FastCorrectionRule } from "@/types/settings";

const settings = defineModel<AppSettingsData>("settings", { required: true });
const props = defineProps<{
  saving: boolean;
  save: () => Promise<boolean>;
}>();

const dialog = ref<HTMLDialogElement | null>(null);
const draftRules = ref<FastCorrectionRule[]>([]);
const validationMessage = ref("");

function cloneRules(rules: FastCorrectionRule[]): FastCorrectionRule[] {
  return rules.map((rule) => ({ ...rule }));
}

function openRules() {
  draftRules.value = cloneRules(settings.value.fast_correction_rules);
  validationMessage.value = "";
  dialog.value?.showModal();
}

function closeRules() {
  if (!props.saving) dialog.value?.close();
}

function addRule() {
  const catchAllIndex = draftRules.value.findIndex(
    (rule) => rule.model_pattern.trim() === "*",
  );
  const insertAt =
    catchAllIndex === -1 ? draftRules.value.length : catchAllIndex;
  draftRules.value.splice(insertAt, 0, {
    model_pattern: "",
    source_multiplier: "2",
    target_multiplier: "2.5",
  });
}

function removeRule(index: number) {
  draftRules.value.splice(index, 1);
}

function moveRule(index: number, offset: -1 | 1) {
  const target = index + offset;
  if (target < 0 || target >= draftRules.value.length) return;
  const [rule] = draftRules.value.splice(index, 1);
  if (rule) draftRules.value.splice(target, 0, rule);
}

function validateRules() {
  for (const [index, rule] of draftRules.value.entries()) {
    const position = index + 1;
    const source = Number(rule.source_multiplier);
    const target = Number(rule.target_multiplier);
    if (!rule.model_pattern.trim()) {
      return `第 ${position} 条规则必须填写模型匹配`;
    }
    if (!Number.isFinite(source) || source < 0.01 || source > 100) {
      return `第 ${position} 条规则的 Sub2API FAST 倍率必须在 0.01 到 100 之间`;
    }
    if (!Number.isFinite(target) || target < 0.01 || target > 100) {
      return `第 ${position} 条规则的修正目标倍率必须在 0.01 到 100 之间`;
    }
    if (target < source) {
      return `第 ${position} 条规则的修正目标倍率不能小于源倍率`;
    }
  }
  return "";
}

async function saveRules() {
  validationMessage.value = validateRules();
  if (validationMessage.value) return;
  settings.value.fast_correction_rules = cloneRules(draftRules.value);
  if (await props.save()) dialog.value?.close();
}
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body">
      <h2 class="card-title">
        <AppIcon name="bolt" class="size-5" />FAST 修正
      </h2>

      <div class="flex items-center justify-between gap-4">
        <SettingLabel
          label="启用 FAST 模型修正"
          help="只影响新采样。已保存的历史 FAST 修正事实会永久保留并继续参与重放。"
        />
        <input
          v-model="settings.fast_correction_enabled"
          type="checkbox"
          class="toggle shrink-0 toggle-sm"
        />
      </div>

      <div class="alert items-start text-sm alert-info">
        <AppIcon name="information-circle" class="mt-0.5 size-5 shrink-0" />
        <span>
          建议优先在 Sub2API 的渠道模型定价中配置 FAST 倍率。Sub2API
          没有统一倍率入口时，可在这里按模型补足差额。
        </span>
      </div>

      <div
        v-if="settings.fast_correction_rebuild_recommended"
        class="alert items-start text-sm alert-warning"
      >
        <AppIcon name="exclamation-triangle" class="mt-0.5 size-5 shrink-0" />
        <span>
          当前周期有
          {{ settings.fast_correction_missing_intervals }}
          个采样区间缺少 FAST
          事实。旧记录无法证明完整请求日志时会保持未知，不会根据当前可查询数据推测或覆盖。
        </span>
      </div>

      <div class="flex flex-wrap gap-2">
        <button type="button" class="btn btn-outline btn-sm" @click="openRules">
          <AppIcon name="adjustments-horizontal" class="size-4" />配置模型规则
        </button>
        <button
          type="button"
          class="btn btn-primary btn-sm"
          :disabled="saving"
          @click="save"
        >
          <span v-if="saving" class="loading loading-xs loading-spinner"></span>
          <AppIcon v-else name="check" class="size-4" />保存 FAST 设置
        </button>
      </div>
    </div>
  </section>

  <Teleport to="body">
    <dialog ref="dialog" class="modal">
      <div
        class="modal-box flex max-h-[calc(100dvh-2rem)] max-w-4xl flex-col overflow-hidden p-0"
      >
        <header
          class="flex shrink-0 items-center justify-between gap-4 border-b border-base-300 px-5 py-4 sm:px-6"
        >
          <h2 class="text-lg font-bold">FAST 模型修正规则</h2>
          <button
            type="button"
            class="btn btn-circle btn-ghost btn-sm"
            aria-label="关闭 FAST 模型修正规则"
            :disabled="saving"
            @click="closeRules"
          >
            <AppIcon name="x-mark" class="size-4" />
          </button>
        </header>

        <div class="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:p-6">
          <div class="alert items-start text-sm">
            <AppIcon name="information-circle" class="mt-0.5 size-5 shrink-0" />
            <span>
              规则从上到下匹配，命中第一条后停止。模型匹配支持星号通配符。默认规则会把所有模型的
              2 倍 FAST 成本修正为 2.5 倍。
            </span>
          </div>

          <article
            v-for="(rule, index) in draftRules"
            :key="index"
            class="rounded-box border border-base-300 bg-base-200 p-4"
          >
            <div class="mb-4 flex items-center justify-between gap-3">
              <span class="badge font-medium badge-neutral"
                >规则 {{ index + 1 }}</span
              >
              <div class="flex items-center gap-1">
                <button
                  type="button"
                  class="btn btn-square btn-ghost btn-xs"
                  title="上移规则"
                  :disabled="index === 0"
                  @click="moveRule(index, -1)"
                >
                  <AppIcon name="chevron-up" class="size-4" />
                </button>
                <button
                  type="button"
                  class="btn btn-square btn-ghost btn-xs"
                  title="下移规则"
                  :disabled="index === draftRules.length - 1"
                  @click="moveRule(index, 1)"
                >
                  <AppIcon name="chevron-up" class="size-4 rotate-180" />
                </button>
                <button
                  type="button"
                  class="btn btn-square btn-ghost text-error btn-xs"
                  title="删除规则"
                  @click="removeRule(index)"
                >
                  <AppIcon name="trash" class="size-4" />
                </button>
              </div>
            </div>

            <div
              class="grid gap-4 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)]"
            >
              <label class="fieldset">
                <span class="fieldset-legend flex items-center gap-1.5">
                  <AppIcon name="cpu-chip" class="size-4" />模型匹配
                </span>
                <input
                  v-model.trim="rule.model_pattern"
                  type="text"
                  class="input w-full font-mono"
                  maxlength="160"
                  placeholder="gpt-5.6*"
                />
              </label>
              <label class="fieldset">
                <span class="fieldset-legend flex items-center gap-1.5">
                  <AppIcon name="circle-stack" class="size-4" />Sub2API FAST
                  倍率
                </span>
                <input
                  v-model="rule.source_multiplier"
                  type="number"
                  class="input w-full tabular-nums"
                  min="0.01"
                  max="100"
                  step="0.1"
                  inputmode="decimal"
                />
              </label>
              <label class="fieldset">
                <span class="fieldset-legend flex items-center gap-1.5">
                  <AppIcon
                    name="adjustments-vertical"
                    class="size-4"
                  />修正目标倍率
                </span>
                <input
                  v-model="rule.target_multiplier"
                  type="number"
                  class="input w-full tabular-nums"
                  min="0.01"
                  max="100"
                  step="0.1"
                  inputmode="decimal"
                />
              </label>
            </div>
          </article>

          <div
            v-if="draftRules.length === 0"
            class="rounded-box border border-dashed border-base-300 py-10 text-center text-sm opacity-60"
          >
            当前没有模型规则。启用 FAST 修正时，没有命中的模型不会增加修正金额。
          </div>

          <button type="button" class="btn btn-outline btn-sm" @click="addRule">
            <AppIcon name="plus" class="size-4" />添加模型规则
          </button>

          <div v-if="validationMessage" class="alert text-sm alert-error">
            <AppIcon name="exclamation-triangle" class="size-5 shrink-0" />
            <span>{{ validationMessage }}</span>
          </div>
        </div>

        <footer
          class="flex shrink-0 justify-end gap-2 border-t border-base-300 px-5 py-4 sm:px-6"
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
            ></span>
            <AppIcon v-else name="check" class="size-4" />保存规则
          </button>
        </footer>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button :disabled="saving">关闭</button>
      </form>
    </dialog>
  </Teleport>
</template>
