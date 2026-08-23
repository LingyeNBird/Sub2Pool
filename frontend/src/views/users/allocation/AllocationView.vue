<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";

import AppIcon from "@/components/common/AppIcon.vue";
import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api, jsonBody } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type {
  MonitoredAccount,
  QuotaAllocationData,
  QuotaAllocationParticipant,
  QuotaAllocationWrite,
} from "@/types";

interface DraftPool {
  key: string;
  id?: number;
  name: string;
  contractRevision: number;
  accountIds: number[];
  allocations: Record<number, string>;
}

interface ContextMenuState {
  accountId: number;
  x: number;
  y: number;
}

const auth = useAuthStore();
const loading = ref(true);
const saving = ref(false);
const dirty = ref(false);
const message = ref("");
const messageTone = ref<"success" | "warning" | "error">("success");
const accounts = ref<MonitoredAccount[]>([]);
const participants = ref<QuotaAllocationParticipant[]>([]);
const draftPools = ref<DraftPool[]>([]);
const selectedAccountIds = ref<Set<number>>(new Set());
const contextMenu = ref<ContextMenuState | null>(null);
const renameDialog = ref<HTMLDialogElement | null>(null);
const renameInput = ref<HTMLInputElement | null>(null);
const renamingPoolKey = ref<string | null>(null);
const renameName = ref("");
let draftSequence = 0;

const accountsById = computed(
  () => new Map(accounts.value.map((account) => [account.id, account])),
);
const selectedCount = computed(() => selectedAccountIds.value.size);
const hasAccounts = computed(() => accounts.value.length > 0);
const hasParticipants = computed(() => participants.value.length > 0);
const hasInvalidPool = computed(() =>
  draftPools.value.some(
    (pool) =>
      poolTotal(pool) > 100 ||
      participants.value.some((participant) =>
        shareIsInvalid(pool, participant.id),
      ),
  ),
);
const canMergeSelection = computed(() => {
  if (selectedCount.value < 2) return false;
  const selected = selectedAccountIds.value;
  const containingPools = draftPools.value.filter((pool) =>
    pool.accountIds.some((accountId) => selected.has(accountId)),
  );
  return !(
    containingPools.length === 1 &&
    containingPools[0].accountIds.length === selected.size &&
    containingPools[0].accountIds.every((accountId) => selected.has(accountId))
  );
});
const splittableSelectedIds = computed(() => {
  const selected = selectedAccountIds.value;
  return draftPools.value.flatMap((pool) =>
    pool.accountIds.length > 1
      ? pool.accountIds.filter((accountId) => selected.has(accountId))
      : [],
  );
});
const canSplitSelection = computed(
  () => splittableSelectedIds.value.length > 0,
);

function draftKey(id?: number) {
  draftSequence += 1;
  return id === undefined ? `new-${draftSequence}` : `pool-${id}`;
}

function allocationMap(
  rows: Array<{ participant_id: number; share_percent: number }>,
) {
  return Object.fromEntries(
    participants.value.map((participant) => {
      const row = rows.find((item) => item.participant_id === participant.id);
      return [participant.id, row ? String(row.share_percent) : "0"];
    }),
  );
}

function hydrate(data: QuotaAllocationData) {
  accounts.value = data.accounts;
  participants.value = data.participants;
  draftPools.value = data.pools.map((pool) => ({
    key: draftKey(pool.id),
    id: pool.id,
    name: pool.name,
    contractRevision: pool.contract_revision,
    accountIds: [...pool.account_ids],
    allocations: allocationMap(pool.allocations),
  }));
  selectedAccountIds.value = new Set();
  contextMenu.value = null;
  dirty.value = false;
}

async function load() {
  loading.value = true;
  message.value = "";
  try {
    hydrate(await api<QuotaAllocationData>("quota-allocation"));
  } catch (error) {
    messageTone.value = "error";
    message.value =
      error instanceof ApiError ? error.message : "加载额度分配失败";
  } finally {
    loading.value = false;
  }
}

function numericShare(pool: DraftPool, participantId: number) {
  const value = Number(pool.allocations[participantId] ?? 0);
  return Number.isFinite(value) ? value : 0;
}
function shareIsInvalid(pool: DraftPool, participantId: number) {
  const raw = pool.allocations[participantId] ?? "0";
  if (raw.trim() === "") return false;
  const value = Number(raw);
  return !Number.isFinite(value) || value < 0 || value > 100;
}

function poolTotal(pool: DraftPool) {
  return participants.value.reduce(
    (total, participant) => total + numericShare(pool, participant.id),
    0,
  );
}
function formattedPoolTotal(pool: DraftPool) {
  return poolTotal(pool)
    .toFixed(3)
    .replace(/\.?0+$/, "");
}

function updateAllocation(
  pool: DraftPool,
  participantId: number,
  event: Event,
) {
  pool.allocations[participantId] = (event.target as HTMLInputElement).value;
  dirty.value = true;
  message.value = "";
}

function accountName(accountId: number) {
  return accountsById.value.get(accountId)?.name ?? `账号 ${accountId}`;
}

function isSelected(accountId: number) {
  return selectedAccountIds.value.has(accountId);
}

function toggleAccount(accountId: number) {
  if (!auth.isStaff) return;
  const next = new Set(selectedAccountIds.value);
  if (next.has(accountId)) next.delete(accountId);
  else next.add(accountId);
  selectedAccountIds.value = next;
  contextMenu.value = null;
}

function clearSelection() {
  selectedAccountIds.value = new Set();
  contextMenu.value = null;
}

function poolForAccount(accountId: number) {
  return draftPools.value.find((pool) => pool.accountIds.includes(accountId));
}

function allocationsMatch(pools: DraftPool[]) {
  if (pools.length <= 1) return true;
  const signature = (pool: DraftPool) =>
    participants.value
      .map((participant) => numericShare(pool, participant.id))
      .join("|");
  const first = signature(pools[0]);
  return pools.every((pool) => signature(pool) === first);
}

function nextMixedPoolName() {
  const used = new Set(draftPools.value.map((pool) => pool.name));
  let index = 1;
  while (used.has(`混池 ${index}`)) index += 1;
  return `混池 ${index}`;
}

function mergeSelected() {
  if (!canMergeSelection.value) return;
  const selected = selectedAccountIds.value;
  const flatOrder = draftPools.value.flatMap((pool) => pool.accountIds);
  const selectedIds = flatOrder.filter((accountId) => selected.has(accountId));
  const sourcePools = draftPools.value.filter((pool) =>
    pool.accountIds.some((accountId) => selected.has(accountId)),
  );
  const inherit = allocationsMatch(sourcePools);
  const inheritedAllocations = inherit
    ? { ...sourcePools[0].allocations }
    : Object.fromEntries(
        participants.value.map((participant) => [participant.id, "0"]),
      );
  const firstSourceIndex = draftPools.value.findIndex((pool) =>
    pool.accountIds.some((accountId) => selected.has(accountId)),
  );
  const nextPools: DraftPool[] = [];
  draftPools.value.forEach((pool, index) => {
    if (index === firstSourceIndex) {
      nextPools.push({
        key: draftKey(),
        name: nextMixedPoolName(),
        contractRevision: 1,
        accountIds: selectedIds,
        allocations: inheritedAllocations,
      });
    }
    const remaining = pool.accountIds.filter(
      (accountId) => !selected.has(accountId),
    );
    if (remaining.length) nextPools.push({ ...pool, accountIds: remaining });
  });
  draftPools.value = nextPools;
  clearSelection();
  dirty.value = true;
  messageTone.value = inherit ? "success" : "warning";
  message.value = inherit
    ? `已将 ${selectedIds.length} 个来源合并为一个混池，保存后生效。`
    : "所选来源的原合同不同，新混池份额已清空，请重新分配后保存。";
}

function splitAccounts(accountIds: Iterable<number>) {
  const requested = new Set(accountIds);
  const splitIds: number[] = [];
  const nextPools: DraftPool[] = [];

  for (const pool of draftPools.value) {
    const extracted =
      pool.accountIds.length > 1
        ? pool.accountIds.filter((accountId) => requested.has(accountId))
        : [];
    if (!extracted.length) {
      nextPools.push(pool);
      continue;
    }

    const remaining = pool.accountIds.filter(
      (accountId) => !requested.has(accountId),
    );
    if (remaining.length) {
      nextPools.push({
        ...pool,
        name:
          remaining.length === 1
            ? `${accountName(remaining[0])} 独立池`
            : pool.name,
        accountIds: remaining,
      });
    }
    for (const accountId of extracted) {
      nextPools.push({
        key: draftKey(),
        name: `${accountName(accountId)} 独立池`,
        contractRevision: 1,
        accountIds: [accountId],
        allocations: { ...pool.allocations },
      });
    }
    splitIds.push(...extracted);
  }

  if (!splitIds.length) return;
  draftPools.value = nextPools;
  clearSelection();
  dirty.value = true;
  messageTone.value = "success";
  message.value = `已将 ${splitIds.length} 个来源从混池拆出，保存后生效。`;
}

function splitSelected() {
  splitAccounts(splittableSelectedIds.value);
}

function splitAccount(accountId: number) {
  splitAccounts([accountId]);
}

function openRenamePool(pool: DraftPool) {
  if (!auth.isStaff || pool.accountIds.length <= 1) return;
  renamingPoolKey.value = pool.key;
  renameName.value = pool.name;
  message.value = "";
  contextMenu.value = null;
  renameDialog.value?.showModal();
  renameInput.value?.select();
}

function closeRenamePool() {
  renameDialog.value?.close();
}

function applyPoolRename() {
  const pool = draftPools.value.find(
    (item) => item.key === renamingPoolKey.value,
  );
  const name = renameName.value.trim();
  if (!pool || pool.accountIds.length <= 1 || !name) return;
  if (pool.name !== name) {
    pool.name = name;
    dirty.value = true;
    message.value = "";
  }
  closeRenamePool();
}

function openContextMenu(event: MouseEvent, accountId: number) {
  if (!auth.isStaff) return;
  event.preventDefault();
  if (!selectedAccountIds.value.has(accountId)) {
    selectedAccountIds.value = new Set([accountId]);
  }
  const menuWidth = 208;
  const menuHeight = 112;
  contextMenu.value = {
    accountId,
    x: Math.min(event.clientX, window.innerWidth - menuWidth - 12),
    y: Math.min(event.clientY, window.innerHeight - menuHeight - 12),
  };
}

function closeContextMenu() {
  contextMenu.value = null;
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    closeContextMenu();
    clearSelection();
  }
}

async function save() {
  if (!auth.isStaff || !dirty.value || hasInvalidPool.value) return;
  saving.value = true;
  message.value = "";
  const payload: QuotaAllocationWrite = {
    pools: draftPools.value.map((pool) => ({
      ...(pool.id === undefined ? {} : { id: pool.id }),
      name: pool.name,
      account_ids: pool.accountIds,
      allocations: participants.value
        .map((participant) => ({
          participant_id: participant.id,
          share_percent: numericShare(pool, participant.id),
        }))
        .filter((allocation) => allocation.share_percent > 0),
    })),
  };
  try {
    hydrate(
      await api<QuotaAllocationData>("quota-allocation", {
        method: "PUT",
        body: jsonBody(payload),
      }),
    );
    messageTone.value = "success";
    message.value =
      "额度池和参与者份额已保存。现有账号观测会按新分配方案立即重算余额建议。";
  } catch (error) {
    messageTone.value = "error";
    message.value =
      error instanceof ApiError ? error.message : "保存额度分配失败";
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  window.addEventListener("click", closeContextMenu);
  window.addEventListener("keydown", handleKeydown);
  window.addEventListener("resize", closeContextMenu);
  void load();
});

onUnmounted(() => {
  window.removeEventListener("click", closeContextMenu);
  window.removeEventListener("keydown", handleKeydown);
  window.removeEventListener("resize", closeContextMenu);
});
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">额度管理</RouterLink></li>
          <li><h1>额度分配</h1></li>
        </ul>
      </div>
      <p class="mt-1 text-sm opacity-60">
        左键选择来源后可合并或拆出；混池名称可重命名；数字按百分比保存，无需输入百分号。
      </p>
    </div>
    <button
      v-if="auth.isStaff"
      class="btn btn-primary btn-sm"
      :disabled="!dirty || saving || hasInvalidPool"
      @click="save"
    >
      <span v-if="saving" class="loading loading-xs loading-spinner"></span>
      <AppIcon v-else name="check" class="size-4" />
      {{ saving ? "保存中" : "保存分配" }}
    </button>
  </PageShellHeader>

  <div
    v-if="message"
    role="alert"
    class="col-span-12 alert"
    :class="{
      'alert-success': messageTone === 'success',
      'alert-warning': messageTone === 'warning',
      'alert-error': messageTone === 'error',
    }"
  >
    <AppIcon
      :name="
        messageTone === 'error' ? 'exclamation-triangle' : 'information-circle'
      "
      class="size-5"
    />
    <span>{{ message }}</span>
  </div>

  <section class="col-span-12 space-y-3">
    <div
      v-if="auth.isStaff && selectedCount"
      class="alert alert-vertical sm:alert-horizontal"
    >
      <div class="flex items-center gap-2">
        <span class="badge badge-sm badge-primary">{{ selectedCount }}</span>
        <span>个来源已选中</span>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          class="btn btn-primary btn-sm"
          :disabled="!canMergeSelection"
          @click.stop="mergeSelected"
        >
          <AppIcon name="link" class="size-4" />
          合并为混池
        </button>
        <button
          class="btn btn-sm"
          :disabled="!canSplitSelection"
          @click.stop="splitSelected"
        >
          <AppIcon name="arrows-right-left" class="size-4" />
          拆出混池
        </button>
        <button class="btn btn-ghost btn-sm" @click.stop="clearSelection">
          取消选择
        </button>
      </div>
    </div>

    <div v-if="loading" class="card bg-base-200 shadow-xs">
      <div class="card-body items-center py-16">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
    </div>

    <div
      v-else-if="!hasAccounts"
      class="card border border-dashed border-base-300 bg-base-200 shadow-xs"
    >
      <div class="card-body items-center py-14 text-center">
        <AppIcon name="server" class="size-9 opacity-30" />
        <h2 class="mt-2 card-title">尚未添加监控账号</h2>
        <p class="text-sm opacity-60">
          先在系统设置中添加来源账号，再配置额度分配。
        </p>
      </div>
    </div>

    <div
      v-else-if="!hasParticipants"
      class="card border border-dashed border-base-300 bg-base-200 shadow-xs"
    >
      <div class="card-body items-center py-14 text-center">
        <AppIcon name="user-group" class="size-9 opacity-30" />
        <h2 class="mt-2 card-title">尚未添加参与者</h2>
        <p class="text-sm opacity-60">
          先添加 Sub2API 参与者，表格才会出现分配列。
        </p>
        <RouterLink v-if="auth.isStaff" to="/participants" class="btn btn-sm">
          前往参与者
        </RouterLink>
      </div>
    </div>

    <div
      v-else
      class="overflow-x-auto rounded-box border border-base-content/25 bg-base-100 shadow-xs"
    >
      <table class="allocation-table table min-w-max">
        <thead>
          <tr>
            <th
              class="source-header sticky left-0 z-30 min-w-56 bg-base-200 p-0"
            >
              <div
                class="diagonal-header relative h-24 min-w-56 overflow-hidden"
              >
                <span class="absolute top-3 right-4">参与者</span>
                <span class="absolute bottom-3 left-4">来源账号</span>
              </div>
            </th>
            <th
              v-for="participant in participants"
              :key="participant.id"
              class="min-w-44 bg-base-200 text-center"
              :class="{ 'opacity-50': !participant.enabled }"
            >
              <div class="font-semibold">{{ participant.name }}</div>
              <div
                class="mt-1 max-w-40 truncate text-xs font-normal opacity-60"
              >
                {{ participant.sub2api_identity }}
              </div>
              <span
                v-if="participant.is_owner"
                class="mt-2 badge badge-xs badge-neutral"
              >
                车主
              </span>
              <span
                v-if="!participant.enabled"
                class="mt-2 badge badge-ghost badge-xs"
              >
                已停用
              </span>
            </th>
          </tr>
        </thead>

        <tbody
          v-for="pool in draftPools"
          :key="pool.key"
          :class="{ 'mixed-pool': pool.accountIds.length > 1 }"
        >
          <tr
            v-for="(accountId, accountIndex) in pool.accountIds"
            :key="accountId"
            :class="{ 'bg-primary/10': isSelected(accountId) }"
            :tabindex="auth.isStaff ? 0 : -1"
            @click="toggleAccount(accountId)"
            @keydown.enter.prevent="toggleAccount(accountId)"
            @keydown.space.prevent="toggleAccount(accountId)"
            @contextmenu="openContextMenu($event, accountId)"
          >
            <th
              class="source-cell sticky left-0 z-20 min-w-56 bg-base-100 p-0"
              :class="[
                { 'bg-primary/10': isSelected(accountId) },
                {
                  'pool-first':
                    pool.accountIds.length > 1 && accountIndex === 0,
                },
                {
                  'pool-last':
                    pool.accountIds.length > 1 &&
                    accountIndex === pool.accountIds.length - 1,
                },
              ]"
            >
              <div
                class="relative flex min-h-24 items-center gap-3 px-5 py-4 text-left"
              >
                <div
                  v-if="pool.accountIds.length > 1 && accountIndex === 0"
                  class="absolute top-2 left-3 flex items-center gap-1.5"
                >
                  <span
                    class="badge max-w-40 badge-sm badge-success"
                    :title="pool.name"
                  >
                    <span class="truncate">{{ pool.name }}</span>
                  </span>
                  <button
                    v-if="auth.isStaff"
                    type="button"
                    class="btn btn-circle btn-ghost btn-xs"
                    :aria-label="`重命名混池“${pool.name}”`"
                    title="重命名混池"
                    @click.stop="openRenamePool(pool)"
                  >
                    <AppIcon name="pencil" class="size-3.5" />
                  </button>
                  <span
                    class="badge shrink-0 badge-sm"
                    :class="
                      poolTotal(pool) > 100 ? 'badge-error' : 'badge-ghost'
                    "
                  >
                    {{ auth.isStaff ? "合计" : "当前可见合计" }}
                    {{ formattedPoolTotal(pool) }}%
                  </span>
                </div>
                <span
                  class="flex size-5 shrink-0 items-center justify-center rounded-full border border-base-300"
                  :class="{
                    'border-primary bg-primary text-primary-content':
                      isSelected(accountId),
                  }"
                >
                  <AppIcon
                    v-if="isSelected(accountId)"
                    name="check"
                    class="size-3"
                  />
                </span>
                <div
                  class="min-w-0"
                  :class="{
                    'pt-4': pool.accountIds.length > 1 && accountIndex === 0,
                  }"
                >
                  <div class="flex min-w-0 items-center gap-2">
                    <span class="min-w-0 truncate font-semibold">
                      {{ accountName(accountId) }}
                    </span>
                    <span
                      v-if="pool.accountIds.length === 1"
                      class="badge shrink-0 badge-sm"
                      :class="
                        poolTotal(pool) > 100 ? 'badge-error' : 'badge-ghost'
                      "
                    >
                      {{ auth.isStaff ? "合计" : "当前可见合计" }}
                      {{ formattedPoolTotal(pool) }}%
                    </span>
                  </div>
                  <div class="mt-1 text-xs font-normal opacity-50">
                    上游 ID
                    {{ accountsById.get(accountId)?.external_account_id }}
                  </div>
                </div>
              </div>
            </th>

            <template v-if="accountIndex === 0">
              <td
                v-for="(participant, participantIndex) in participants"
                :key="participant.id"
                :rowspan="pool.accountIds.length"
                class="allocation-cell min-w-44 p-4 align-middle"
                :class="[
                  { 'pool-first': pool.accountIds.length > 1 },
                  { 'pool-last': pool.accountIds.length > 1 },
                  {
                    'pool-right':
                      pool.accountIds.length > 1 &&
                      participantIndex === participants.length - 1,
                  },
                ]"
                @click.stop
                @contextmenu.stop.prevent
              >
                <label
                  class="input mx-auto flex w-28 items-center gap-1"
                  :class="{
                    'input-error': shareIsInvalid(pool, participant.id),
                  }"
                >
                  <input
                    :value="pool.allocations[participant.id] ?? '0'"
                    type="number"
                    min="0"
                    max="100"
                    step="0.001"
                    inputmode="decimal"
                    class="grow text-center tabular-nums"
                    :disabled="!auth.isStaff"
                    :aria-label="`${pool.name} 分配给 ${participant.name} 的百分比`"
                    @input="updateAllocation(pool, participant.id, $event)"
                    @keydown.enter.prevent="
                      ($event.target as HTMLInputElement).blur()
                    "
                  />
                  <span class="text-xs opacity-40">%</span>
                </label>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div>

    <div
      class="flex flex-wrap items-center justify-between gap-3 text-xs opacity-60"
    >
      <span>每个账号必须且只能属于一个池；单账号本身就是独立池。</span>
      <span v-if="dirty" class="badge badge-sm badge-warning"
        >有未保存更改</span
      >
    </div>
  </section>

  <Teleport to="body">
    <dialog ref="renameDialog" class="modal">
      <div class="modal-box w-[calc(100vw-2rem)] max-w-md">
        <h2 class="text-lg font-bold">重命名混池</h2>
        <p class="mt-1 text-sm opacity-60">
          名称会显示在额度分配表和参与者权益来源中。
        </p>
        <form class="mt-4" @submit.prevent="applyPoolRename">
          <fieldset class="fieldset">
            <label class="label" for="mixed-pool-name">混池名称</label>
            <input
              id="mixed-pool-name"
              ref="renameInput"
              v-model="renameName"
              class="input w-full"
              maxlength="160"
              required
            />
          </fieldset>
          <div class="modal-action">
            <button type="button" class="btn" @click="closeRenamePool">
              取消
            </button>
            <button
              type="submit"
              class="btn btn-primary"
              :disabled="!renameName.trim()"
            >
              应用名称
            </button>
          </div>
        </form>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>关闭</button>
      </form>
    </dialog>
  </Teleport>

  <div
    v-if="contextMenu"
    class="fixed z-50"
    :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
    @click.stop
  >
    <ul
      class="menu w-52 rounded-box border border-base-300 bg-base-100 p-2 shadow-xl"
    >
      <li>
        <button :disabled="!canMergeSelection" @click="mergeSelected">
          <AppIcon name="link" class="size-4" />
          合并为混池
        </button>
      </li>
      <li
        v-if="
          (poolForAccount(contextMenu.accountId)?.accountIds.length ?? 0) > 1
        "
      >
        <button @click="splitAccount(contextMenu.accountId)">
          <AppIcon name="arrows-right-left" class="size-4" />
          拆出为独立池
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.diagonal-header::after {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to top right,
    transparent calc(50% - 0.75px),
    color-mix(in oklab, currentColor 55%, transparent) 50%,
    transparent calc(50% + 0.75px)
  );
  content: "";
  pointer-events: none;
}

.allocation-table thead th {
  border-bottom: 2px solid
    color-mix(in oklab, var(--color-base-content) 32%, transparent);
}

.allocation-table .allocation-cell {
  border-left: 1px solid
    color-mix(in oklab, var(--color-base-content) 22%, transparent);
}

.allocation-table tbody > tr > * {
  border-bottom-color: color-mix(
    in oklab,
    var(--color-base-content) 22%,
    transparent
  );
}

.allocation-table tbody + tbody > tr:first-child > * {
  border-top: 2px solid
    color-mix(in oklab, var(--color-base-content) 32%, transparent);
}

.allocation-table tbody.mixed-pool .pool-first {
  border-top: 2px solid var(--color-success);
}

.allocation-table tbody.mixed-pool .pool-last {
  border-bottom: 2px solid var(--color-success);
}

.allocation-table tbody.mixed-pool .source-cell {
  border-left: 2px solid var(--color-success);
}

.allocation-table tbody.mixed-pool .source-cell:not(.pool-last) {
  border-bottom-style: dashed;
  border-bottom-color: color-mix(
    in oklab,
    var(--color-success) 45%,
    transparent
  );
}

.allocation-table tbody.mixed-pool .pool-right {
  border-right: 2px solid var(--color-success);
}

.allocation-table tbody.mixed-pool .allocation-cell {
  background: color-mix(
    in oklab,
    var(--color-success) 5%,
    var(--color-base-100)
  );
}

.allocation-table tbody:not(.mixed-pool):last-child > tr:last-child > * {
  border-bottom: 0;
}
</style>
