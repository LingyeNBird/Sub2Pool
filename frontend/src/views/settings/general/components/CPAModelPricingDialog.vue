<script setup lang="ts">
import { ref } from "vue";

import type { CPAModelPricing } from "@/types/settings";

type PriceRow = {
  model: string;
  input: string;
  cached_input: string;
  output: string;
};

const props = defineProps<{
  pricing: CPAModelPricing;
  savePricing: (pricing: CPAModelPricing) => Promise<string | null>;
}>();
const dialog = ref<HTMLDialogElement | null>(null);
const rows = ref<PriceRow[]>([]);
const error = ref("");
const saving = ref(false);

function open() {
  rows.value = Object.entries(props.pricing)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([model, price]) => ({
      model,
      input: String(price.input),
      cached_input: String(price.cached_input),
      output: String(price.output),
    }));
  error.value = "";
  dialog.value?.showModal();
}

function addRow() {
  rows.value.push({ model: "", input: "0", cached_input: "0", output: "0" });
}

async function save() {
  const pricing: CPAModelPricing = {};
  for (const row of rows.value) {
    const model = row.model.trim();
    if (!model) {
      error.value = "模型名不能为空";
      return;
    }
    if (model in pricing) {
      error.value = `模型 ${model} 重复`;
      return;
    }
    for (const field of ["input", "cached_input", "output"] as const) {
      const value = Number(row[field]);
      if (!Number.isFinite(value) || value < 0) {
        error.value = `${model} 的价格必须是非负数`;
        return;
      }
    }
    pricing[model] = {
      input: row.input,
      cached_input: row.cached_input,
      output: row.output,
    };
  }
  if (!Object.keys(pricing).length) {
    error.value = "至少保留一个模型价格";
    return;
  }
  error.value = "";
  saving.value = true;
  try {
    const failure = await props.savePricing(pricing);
    if (failure) {
      error.value = failure;
      return;
    }
    dialog.value?.close();
  } catch {
    error.value = "保存模型价格失败";
  } finally {
    saving.value = false;
  }
}
function preventClose(event: Event) {
  if (saving.value) event.preventDefault();
}

defineExpose({ open });
</script>

<template>
  <dialog ref="dialog" class="modal" @cancel="preventClose">
    <div class="modal-box max-w-5xl">
      <div class="flex items-start justify-between gap-4">
        <div>
          <h3 class="text-lg font-semibold">CPA 模型价格</h3>
          <p class="mt-1 text-sm leading-6 opacity-65">
            单位均为美元 / 百万
            Token。保存后的手动价格是权威值，版本升级不会自动覆盖。
          </p>
        </div>
        <form method="dialog">
          <button
            class="btn btn-circle btn-ghost btn-sm"
            aria-label="关闭"
            :disabled="saving"
          >
            ✕
          </button>
        </form>
      </div>

      <div v-if="error" class="mt-4 alert alert-error">
        <AppIcon name="exclamation-triangle" class="size-5" />
        <span>{{ error }}</span>
      </div>

      <fieldset class="contents" :disabled="saving">
        <div
          class="mt-5 max-h-[60vh] overflow-auto rounded-box border border-base-300"
        >
          <table class="table table-sm">
            <thead class="sticky top-0 z-10 bg-base-200">
              <tr>
                <th class="min-w-52">模型</th>
                <th class="min-w-36">输入</th>
                <th class="min-w-36">缓存输入</th>
                <th class="min-w-36">输出</th>
                <th class="w-16"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in rows" :key="index">
                <td>
                  <input v-model="row.model" class="input w-full input-sm" />
                </td>
                <td>
                  <input
                    v-model="row.input"
                    type="number"
                    min="0"
                    step="0.001"
                    class="input w-full input-sm"
                  />
                </td>
                <td>
                  <input
                    v-model="row.cached_input"
                    type="number"
                    min="0"
                    step="0.001"
                    class="input w-full input-sm"
                  />
                </td>
                <td>
                  <input
                    v-model="row.output"
                    type="number"
                    min="0"
                    step="0.001"
                    class="input w-full input-sm"
                  />
                </td>
                <td>
                  <button
                    type="button"
                    class="btn btn-square btn-ghost btn-sm"
                    aria-label="删除模型价格"
                    @click="rows.splice(index, 1)"
                  >
                    <AppIcon name="trash" class="size-4" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="modal-action justify-between">
          <button type="button" class="btn btn-ghost btn-sm" @click="addRow">
            <AppIcon name="plus" class="size-4" />添加模型
          </button>
          <div class="flex gap-2">
            <form method="dialog">
              <button class="btn btn-sm">取消</button>
            </form>
            <button type="button" class="btn btn-primary btn-sm" @click="save">
              <span
                v-if="saving"
                class="loading loading-xs loading-spinner"
              ></span>
              保存价格
            </button>
          </div>
        </div>
      </fieldset>
    </div>
    <form method="dialog" class="modal-backdrop">
      <button :disabled="saving">关闭</button>
    </form>
  </dialog>
</template>
