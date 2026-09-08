<script setup lang="ts">
import { computed, ref } from "vue";
import { api, ApiError, jsonBody } from "@/services/api";
import type { CPAPricingInventory, CPAPricingSync } from "@/types/cpaPricing";

import type { CPAModelPricing } from "@/types/settings";

type PriceRow = {
  model: string;
  input: string | number;
  cached_input: string | number;
  output: string | number;
};

const props = defineProps<{
  pricing?: CPAModelPricing;
  accountId?: number;
  savePricing?: (pricing: CPAModelPricing) => Promise<string | null>;
}>();
const dialog = ref<HTMLDialogElement | null>(null);
const rows = ref<PriceRow[]>([]);
const error = ref("");
const saving = ref(false);

const emit = defineEmits<{ saved: [pricing: CPAModelPricing] }>();
const inventory = ref<CPAPricingInventory | null>(null);
const loading = ref(false);
const result = ref<CPAPricingSync | null>(null);
const query = ref("");
const missingOnly = ref(false);
const initialRows = ref("");
const dirty = computed(() => JSON.stringify(rows.value) !== initialRows.value);
const visibleRows = computed(() =>
  rows.value
    .map((row, index) => ({ row, index }))
    .filter(
      ({ row }) =>
        row.model.toLowerCase().includes(query.value.toLowerCase()) &&
        (!missingOnly.value || isMissing(row.model)),
    ),
);
const endpoint = () =>
  `settings/cpa-pricing${props.accountId ? `?account_id=${props.accountId}` : ""}`;
function isMissing(model: string) {
  return (
    inventory.value?.models.some(
      (item) => item.model === model && item.missing,
    ) ?? false
  );
}
function count(model: string) {
  return (
    inventory.value?.models.find((item) => item.model === model)
      ?.request_count ?? 0
  );
}
function setRows(pricing: CPAModelPricing) {
  rows.value = Object.entries(pricing).map(([model, price]) => ({
    model,
    input: String(price.input),
    cached_input: String(price.cached_input),
    output: String(price.output),
  }));
  for (const item of inventory.value?.models ?? []) {
    if (item.missing && !rows.value.some((row) => row.model === item.model))
      rows.value.push({
        model: item.model,
        input: "",
        cached_input: "",
        output: "",
      });
  }
  rows.value.sort(
    (a, b) =>
      Number(isMissing(b.model)) - Number(isMissing(a.model)) ||
      a.model.localeCompare(b.model),
  );
  initialRows.value = JSON.stringify(rows.value);
}
async function open(syncNow = false) {
  error.value = "";
  result.value = null;
  inventory.value = null;
  query.value = "";
  missingOnly.value = false;
  setRows(props.pricing ?? {});
  dialog.value?.showModal();
  loading.value = true;
  try {
    inventory.value = await api<CPAPricingInventory>(endpoint());
    setRows(inventory.value.pricing);
  } catch (reason) {
    error.value =
      reason instanceof ApiError ? reason.message : "价格列表加载失败";
  } finally {
    loading.value = false;
  }
  if (syncNow && inventory.value) await sync();
}
async function sync() {
  if (saving.value || loading.value || dirty.value) return;
  saving.value = true;
  error.value = "";
  result.value = null;
  try {
    result.value = await api<CPAPricingSync>(endpoint(), { method: "POST" });
    inventory.value = result.value;
    setRows(result.value.pricing);
    emit("saved", result.value.pricing);
  } catch (reason) {
    error.value = reason instanceof ApiError ? reason.message : "价格同步失败";
  } finally {
    saving.value = false;
  }
}

function addRow() {
  missingOnly.value = false;
  query.value = "";
  rows.value.push({ model: "", input: "", cached_input: "", output: "" });
}

async function save() {
  if (!inventory.value || loading.value || saving.value) return;
  const pricing: CPAModelPricing = {};
  for (const row of rows.value) {
    const model = row.model.trim();
    if (!model) {
      error.value = "模型名不能为空";
      return;
    }
    if (
      isMissing(model) &&
      [row.input, row.cached_input, row.output].every(
        (value) => String(value).trim() === "",
      )
    )
      continue;
    if (model in pricing) {
      error.value = `模型 ${model} 重复`;
      return;
    }
    for (const field of ["input", "cached_input", "output"] as const) {
      const value = Number(row[field]);
      if (!String(row[field]).trim() || !Number.isFinite(value) || value < 0) {
        error.value = `${model} 的价格必须是非负数`;
        return;
      }
    }
    pricing[model] = {
      input: String(row.input),
      cached_input: String(row.cached_input),
      output: String(row.output),
    };
  }
  if (!Object.keys(pricing).length) {
    error.value = "至少保留一个模型价格";
    return;
  }
  error.value = "";
  saving.value = true;
  try {
    let failure: string | null = null;
    if (props.savePricing) failure = await props.savePricing(pricing);
    else
      await api("settings", {
        method: "PATCH",
        body: jsonBody({ cpa_model_pricing: pricing }),
      });
    if (failure) {
      error.value = failure;
      return;
    }
    emit("saved", pricing);
    dialog.value?.close();
  } catch {
    error.value = "保存模型价格失败";
  } finally {
    saving.value = false;
  }
}
function preventClose(event: Event) {
  if (saving.value || loading.value) event.preventDefault();
}

defineExpose({ open });
</script>

<template>
  <dialog
    id="cpa-pricing-dialog"
    ref="dialog"
    class="modal"
    @cancel="preventClose"
  >
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
            :disabled="saving || loading"
          >
            ✕
          </button>
        </form>
      </div>

      <div v-if="error" class="mt-4 alert alert-error">
        <AppIcon name="exclamation-triangle" class="size-5" />
        <span>{{ error }}</span>
      </div>

      <div class="mt-4 flex flex-wrap items-center gap-3">
        <span v-if="inventory" class="badge badge-outline"
          >缺价 {{ inventory.missing_model_count }} 个模型 ·
          {{ inventory.unpriced_request_count }} 次请求</span
        >
        <button
          class="btn btn-primary btn-sm"
          :disabled="
            saving || loading || dirty || !inventory?.missing_model_count
          "
          @click="sync"
        >
          {{ saving ? "同步 / 重算中…" : "一键同步缺失价格" }}
        </button>
      </div>
      <p class="mt-2 text-sm opacity-65">
        从 models.dev
        同步已采集模型的输入、缓存输入和输出基础价，保留已有价格。FAST
        和长上下文沿用 CPA 现有配置。保存后自动重算历史用量。
      </p>
      <p v-if="inventory" class="mt-2 text-sm opacity-65">
        请求数涵盖{{
          accountId ? "所选账号" : "全部 CPA 账号"
        }}的全部已采集历史；价格配置对所有 CPA 账号生效。 来源：{{
          inventory.source
        }}。
      </p>
      <p v-if="dirty" class="mt-2 text-sm">
        有未保存的编辑，请先保存或重新打开，再同步价格。
      </p>
      <p v-if="loading" role="status" class="mt-2">正在读取缺价模型…</p>
      <div v-if="result" class="mt-3 alert" role="status">
        <div>
          <p>
            已补全 {{ result.added.length }} 个模型，仍有
            {{ result.missing_model_count }} 个模型缺价。{{
              result.added.length ? "历史用量已更新。" : "已有价格保持不变。"
            }}
          </p>
          <p v-for="item in result.added" :key="item.model">
            {{ item.model }} · models.dev / {{ item.source_model }}
          </p>
          <p v-for="item in result.unresolved" :key="item.model">
            {{ item.model }}：{{ item.reason }}
          </p>
        </div>
      </div>
      <fieldset class="contents" :disabled="saving || loading || !inventory">
        <div class="mt-4 flex flex-wrap gap-3">
          <input
            v-model="query"
            class="input input-sm"
            placeholder="搜索模型"
            aria-label="搜索定价模型"
          />
          <button
            class="btn btn-sm"
            :class="{ 'btn-active': missingOnly }"
            @click="missingOnly = !missingOnly"
          >
            只看缺价
          </button>
        </div>
        <div
          class="mt-5 max-h-[60vh] overflow-auto rounded-box border border-base-300"
        >
          <table class="table table-sm">
            <thead class="sticky top-0 z-10 bg-base-200">
              <tr>
                <th class="min-w-52">模型</th>
                <th>请求 / 状态</th>
                <th class="min-w-36">输入</th>
                <th class="min-w-36">缓存输入</th>
                <th class="min-w-36">输出</th>
                <th class="w-16"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="{ row, index } in visibleRows" :key="index">
                <td>
                  <input
                    v-model="row.model"
                    class="input w-full input-sm"
                    aria-label="模型名称"
                  />
                </td>
                <td>
                  {{ count(row.model)
                  }}<span
                    v-if="isMissing(row.model)"
                    class="badge badge-sm badge-warning"
                    >缺价</span
                  >
                </td>
                <td>
                  <input
                    v-model="row.input"
                    aria-label="输入价格"
                    type="number"
                    min="0"
                    step="0.001"
                    class="input w-full input-sm"
                  />
                </td>
                <td>
                  <input
                    v-model="row.cached_input"
                    aria-label="缓存输入价格"
                    type="number"
                    min="0"
                    step="0.001"
                    class="input w-full input-sm"
                  />
                </td>
                <td>
                  <input
                    v-model="row.output"
                    aria-label="输出价格"
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
            <button type="button" class="btn btn-sm" @click="save">
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
      <button :disabled="saving || loading">关闭</button>
    </form>
  </dialog>
</template>
