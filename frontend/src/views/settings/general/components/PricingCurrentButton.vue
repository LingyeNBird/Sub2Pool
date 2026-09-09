<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { api } from "@/services/api";
import PaginationControls from "@/components/common/PaginationControls.vue";

type Kind = "fast" | "model" | "context";
type ModelRow = {
  model: string;
  multiplier: string | null;
  reference: string;
  warning: string;
  prices: Record<string, number | null>;
  ratios: Record<string, string>;
};
type CurrentPricing =
  | {
      kind: "fast";
      free_fast: boolean;
      rows: Array<{ models: string[]; multiplier: number | null }>;
    }
  | { kind: "model"; rows: ModelRow[] }
  | { kind: "context"; enabled: boolean | null };

const props = defineProps<{ kind: Kind; demo: boolean }>();
const titles: Record<Kind, string> = {
  fast: "FAST 倍率",
  model: "模型倍率",
  context: "长上下文阶梯计费",
};
const priceLabels: Record<string, string> = {
  input_price: "输入",
  output_price: "输出",
  cache_write_price: "缓存写入",
  cache_write_1h_price: "一小时缓存写入",
  cache_read_price: "缓存读取",
  image_input_price: "图片输入",
  image_output_price: "图片输出",
  per_request_price: "每次请求",
};
const dialog = ref<HTMLDialogElement | null>(null);
const groups = ref<Array<{ id: number; name: string }>>([]);
const activeGroup = ref<number | null>(null);
const pricing = ref<CurrentPricing | null>(null);
const loadingGroups = ref(false);
const loading = ref(false);
const error = ref("");
const search = ref("");
const page = ref(1);
const pageSize = 10;

function visibleRows<T>(
  rows: T[],
  label: (row: T) => string,
  customized: (row: T) => boolean,
  prioritizeCustomized = true,
) {
  const keyword = search.value.trim().toLowerCase();
  const filtered = rows
    .map((row, id) => ({ row, id, customized: customized(row) }))
    .filter(({ row }) => label(row).toLowerCase().includes(keyword));
  return prioritizeCustomized
    ? filtered.sort(
        (left, right) => Number(right.customized) - Number(left.customized),
      )
    : filtered;
}

const fastRows = computed(() =>
  pricing.value?.kind === "fast"
    ? visibleRows(
        pricing.value.rows,
        (row) => row.models.join(" "),
        (row) => row.multiplier != null,
        false,
      )
    : [],
);
const modelRows = computed(() =>
  pricing.value?.kind === "model"
    ? visibleRows(
        pricing.value.rows,
        (row) => row.model,
        (row) => Object.values(row.prices).some((price) => price != null),
      )
    : [],
);
const total = computed(() =>
  props.kind === "fast" ? fastRows.value.length : modelRows.value.length,
);
const totalPages = computed(() =>
  Math.max(1, Math.ceil(total.value / pageSize)),
);
const fastPage = computed(() =>
  fastRows.value.slice((page.value - 1) * pageSize, page.value * pageSize),
);
const modelPage = computed(() =>
  modelRows.value.slice((page.value - 1) * pageSize, page.value * pageSize),
);
watch(search, () => {
  page.value = 1;
});
watch(totalPages, (maximum) => {
  page.value = Math.min(page.value, maximum);
});
let generation = 0;
let controller: AbortController | null = null;

function cancelRead() {
  generation += 1;
  controller?.abort();
  loading.value = false;
  loadingGroups.value = false;
}

async function selectGroup(id: number) {
  const version = ++generation;
  controller?.abort();
  const request = new AbortController();
  controller = request;
  activeGroup.value = id;
  page.value = 1;
  pricing.value = null;
  error.value = "";
  loading.value = true;
  try {
    await nextTick();
    if (version !== generation) return;
    dialog.value
      ?.querySelector<HTMLElement>(`[data-group-id="${id}"]`)
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
    const result = await api<CurrentPricing>(
      `settings/upstream-pricing?group_id=${id}&kind=${props.kind}`,
      { signal: request.signal },
    );
    if (version === generation) pricing.value = result;
  } catch (cause) {
    if (version === generation && !request.signal.aborted)
      error.value = cause instanceof Error ? cause.message : "读取当前配置失败";
  } finally {
    if (version === generation) loading.value = false;
  }
}

async function refresh() {
  const version = ++generation;
  controller?.abort();
  const request = new AbortController();
  controller = request;
  loadingGroups.value = true;
  loading.value = false;
  error.value = "";
  pricing.value = null;
  try {
    const result = await api<Array<{ id: number; name: string }>>(
      "settings/upstream-pricing?groups=1",
      { signal: request.signal },
    );
    if (version !== generation) return;
    groups.value = result;
    loadingGroups.value = false;
    const id =
      result.find((group) => group.id === activeGroup.value)?.id ??
      result[0]?.id;
    if (id !== undefined) await selectGroup(id);
    else activeGroup.value = null;
  } catch (cause) {
    if (version === generation && !request.signal.aborted)
      error.value = cause instanceof Error ? cause.message : "读取分组失败";
  } finally {
    if (version === generation) loadingGroups.value = false;
  }
}

function open() {
  dialog.value?.showModal();
  void refresh();
}

function moveTab(offset: number) {
  const index = groups.value.findIndex(
    (group) => group.id === activeGroup.value,
  );
  const group =
    groups.value[(index + offset + groups.value.length) % groups.value.length];
  if (!group) return;
  dialog.value
    ?.querySelector<HTMLButtonElement>(`[data-group-id="${group.id}"]`)
    ?.focus();
  void selectGroup(group.id);
}

function factor(value: string | number) {
  return `${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 6 })}×`;
}

onBeforeUnmount(cancelRead);
</script>

<template>
  <button
    type="button"
    class="btn shrink-0 btn-ghost btn-xs"
    :aria-label="`查看当前${titles[kind]}`"
    @click="open"
  >
    查看当前
  </button>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="modal"
      :aria-labelledby="`pricing-current-${kind}-title`"
      @close="cancelRead"
    >
      <div class="modal-box max-w-4xl">
        <div class="flex items-start gap-3">
          <div
            role="tablist"
            aria-label="分组"
            class="tabs tabs-border min-w-0 flex-1 flex-nowrap overflow-x-auto"
          >
            <button
              v-for="group in groups"
              :key="group.id"
              type="button"
              role="tab"
              class="tab max-w-full shrink-0"
              :class="{ 'tab-active': activeGroup === group.id }"
              :aria-selected="activeGroup === group.id"
              :aria-controls="`pricing-current-${kind}-panel`"
              :tabindex="activeGroup === group.id ? 0 : -1"
              :data-group-id="group.id"
              :title="group.name"
              :disabled="loadingGroups"
              @click="selectGroup(group.id)"
              @keydown.right.prevent="moveTab(1)"
              @keydown.left.prevent="moveTab(-1)"
            >
              <span class="truncate">{{ group.name }}</span>
              <span class="ml-1 shrink-0 opacity-50">#{{ group.id }}</span>
            </button>
          </div>
          <form method="dialog">
            <button class="btn btn-ghost btn-sm">关闭</button>
          </form>
        </div>
        <div class="mt-4 flex items-center justify-between gap-3">
          <h3
            :id="`pricing-current-${kind}-title`"
            class="text-lg font-semibold"
          >
            当前{{ titles[kind] }}
          </h3>
          <button
            type="button"
            class="btn btn-outline btn-xs"
            :disabled="loadingGroups || loading"
            @click="refresh"
          >
            刷新
          </button>
        </div>
        <p v-if="demo" class="mt-1 text-xs opacity-60">
          合成演示配置，不访问真实上游。
        </p>
        <p v-if="error" role="alert" class="mt-4 text-sm text-error">
          {{ error }}
        </p>
        <div
          v-if="loadingGroups || loading"
          role="status"
          class="flex items-center gap-2 py-8 text-sm opacity-65"
        >
          <span class="loading loading-sm loading-spinner"></span>正在读取上游…
        </div>
        <p v-else-if="!groups.length && !error" class="py-8 text-sm opacity-60">
          没有可查看的 OpenAI 分组。
        </p>
        <div
          v-else-if="pricing"
          :id="`pricing-current-${kind}-panel`"
          role="tabpanel"
          :aria-label="titles[kind]"
          class="mt-4"
        >
          <div v-if="pricing.kind !== 'context'" class="mb-4 space-y-2">
            <input
              v-model="search"
              type="search"
              class="input w-full input-sm"
              :aria-label="`搜索${titles[kind]}`"
              placeholder="搜索模型名称或匹配规则"
            />
          </div>
          <template v-if="pricing.kind === 'fast'">
            <p class="mb-3 text-sm">
              分组免费 FAST：<strong>{{
                pricing.free_fast ? "开启" : "关闭"
              }}</strong>
            </p>
            <div v-if="fastRows.length" class="overflow-x-auto">
              <table class="table table-sm">
                <thead>
                  <tr>
                    <th>模型匹配</th>
                    <th>当前 FAST 倍率</th>
                    <th>来源</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="{ row, id, customized } in fastPage"
                    :key="id"
                    :class="{
                      'bg-primary/5 ring-1 ring-primary/50 ring-inset':
                        customized,
                    }"
                  >
                    <td class="font-mono">{{ row.models.join(", ") }}</td>
                    <td>
                      {{
                        row.multiplier == null
                          ? "未设置"
                          : factor(row.multiplier)
                      }}
                    </td>
                    <td class="text-xs whitespace-nowrap">
                      {{ customized ? "已设置" : "继承上游" }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="text-sm opacity-65">
              {{
                pricing.rows.length
                  ? "没有匹配的模型或规则。"
                  : "分组未配置 FAST 价卡，使用渠道或上游默认计价。"
              }}
            </p>
            <p class="mt-3 text-xs opacity-60">
              免费 FAST 开关和价卡倍率均按上游原值展示；未设置不等同于 1 倍。
            </p>
          </template>
          <template v-else-if="pricing.kind === 'model'">
            <p class="mb-3 text-xs opacity-65">
              上游保存绝对单价；下方将当前价格与标注的基础价比较，不包含用户或分组折扣。无法确认单一倍率时显示分项值，不猜测。
            </p>
            <p v-if="!modelRows.length" class="text-sm opacity-65">
              {{
                pricing.rows.length
                  ? "没有匹配的模型或规则。"
                  : "分组没有自定义模型价卡，使用渠道或上游默认价格。"
              }}
            </p>
            <details
              v-for="{ row, id, customized } in modelPage"
              :key="id"
              class="mb-2 rounded-box border p-3"
              :class="
                customized
                  ? 'border-primary/70 bg-primary/5 ring-1 ring-primary/20'
                  : 'border-base-300'
              "
            >
              <summary class="cursor-pointer text-sm">
                <span class="font-mono">{{ row.model }}</span
                ><strong class="ml-3">{{
                  row.multiplier === null
                    ? "无统一倍率"
                    : factor(row.multiplier)
                }}</strong>
                <span
                  class="ml-2 text-xs"
                  :class="customized ? 'font-medium' : 'opacity-60'"
                  >{{ customized ? "已设置" : "继承上游" }}</span
                >
              </summary>
              <p class="mt-3 text-xs opacity-65">
                参考：{{ row.reference || "无法确定" }}
              </p>
              <p v-if="row.warning" class="mt-2 text-sm text-warning">
                {{ row.warning }}
              </p>
              <div class="mt-2 overflow-x-auto">
                <table class="table table-xs">
                  <thead>
                    <tr>
                      <th>价格项</th>
                      <th>当前分组单价</th>
                      <th>相对基础价</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(value, field) in row.prices" :key="field">
                      <td>{{ priceLabels[field] || field }}</td>
                      <td>{{ value == null ? "继承上游" : value }}</td>
                      <td>
                        {{
                          row.ratios[field] == null
                            ? "—"
                            : factor(row.ratios[field])
                        }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p class="mt-2 text-xs opacity-50">
                Token 单价单位为美元 / Token；按次价格单位为美元 / 次。
              </p>
            </details>
          </template>
          <template v-else>
            <div class="rounded-box bg-base-200 p-5">
              <span class="text-sm opacity-65">长上下文阶梯计费</span>
              <p class="mt-2 text-lg font-semibold">
                {{
                  pricing.enabled === true
                    ? "启用"
                    : pricing.enabled === false
                      ? "关闭"
                      : "未显式设置（由上游默认决定）"
                }}
              </p>
            </div>
          </template>
          <PaginationControls
            v-if="pricing.kind !== 'context'"
            :page="page"
            :total-pages="totalPages"
            :total="total"
            show-page-summary
            @change="page = $event"
          />
        </div>
      </div>
      <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
    </dialog>
  </Teleport>
</template>
