<script setup lang="ts">
import { computed, ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import type {
  AccountBreakdown,
  AggregateRecommendationSource,
  Participant,
  ParticipantPoolAllocation,
} from "@/types/participants";
import {
  formatCompactPercent,
  formatCurrency,
  formatCurrencyRange,
} from "@/utils/formatters";

import EntitlementProgress from "./EntitlementProgress.vue";

interface EntitlementSourceGroup {
  allocation: ParticipantPoolAllocation;
  accounts: AccountBreakdown[];
  title: string;
  complete: boolean;
  expectedEntitlement: number | null;
  consumedEntitlement: number | null;
  remainingEntitlement: number | null;
  usagePercent: number | null;
}

const props = defineProps<{
  participant: Participant;
  editable: boolean;
}>();

const emit = defineEmits<{
  edit: [participant: Participant];
}>();

const dateTime = useDateTime();
const pressed = ref(false);

function sourceFor(breakdown: AccountBreakdown) {
  return props.participant.snapshot?.sources.find(
    (item) => item.account_id === breakdown.account_id,
  );
}
function accountUsage(account: AccountBreakdown | undefined) {
  return account?.snapshot?.selected_cost ?? account?.latest_selected_cost;
}

function sumSourceField(
  sources: AggregateRecommendationSource[],
  field:
    | "expected_entitlement_usd"
    | "consumed_entitlement_usd"
    | "remaining_entitlement_usd",
) {
  return sources.reduce((total, source) => total + (source[field] ?? 0), 0);
}

const sourceGroups = computed<EntitlementSourceGroup[]>(() =>
  props.participant.pool_allocations.map((allocation) => {
    const accounts = props.participant.account_breakdowns.filter(
      (breakdown) =>
        breakdown.allocated && breakdown.pool_id === allocation.pool_id,
    );
    const activeAccounts = accounts.filter(
      (account) => account.account_enabled,
    );
    const sources = activeAccounts
      .map(sourceFor)
      .filter(
        (source): source is AggregateRecommendationSource => source != null,
      );
    const complete =
      activeAccounts.length > 0 &&
      sources.length === activeAccounts.length &&
      sources.every(
        (source) =>
          source.expected_entitlement_usd != null &&
          source.consumed_entitlement_usd != null,
      );
    const expectedEntitlement = complete
      ? sumSourceField(sources, "expected_entitlement_usd")
      : null;
    const consumedEntitlement = complete
      ? sumSourceField(sources, "consumed_entitlement_usd")
      : null;
    const remainingEntitlement = complete
      ? sumSourceField(sources, "remaining_entitlement_usd")
      : null;
    const usagePercent =
      expectedEntitlement != null &&
      expectedEntitlement > 0 &&
      consumedEntitlement != null
        ? (consumedEntitlement * 100) / expectedEntitlement
        : complete
          ? 0
          : null;
    return {
      allocation,
      accounts,
      title:
        accounts.length === 1 ? accounts[0].account_name : allocation.pool_name,
      complete,
      expectedEntitlement,
      consumedEntitlement,
      remainingEntitlement,
      usagePercent,
    };
  }),
);

function edit() {
  if (props.editable) emit("edit", props.participant);
}
</script>

<template>
  <div class="relative min-w-0 p-3">
    <article
      class="card w-full bg-base-200 shadow-xs"
      :class="[
        editable
          ? 'cursor-pointer transition-transform duration-150 select-none'
          : 'cursor-default',
        { 'scale-[0.99]': editable && pressed },
      ]"
      :role="editable ? 'button' : undefined"
      :tabindex="editable ? 0 : undefined"
      :aria-label="editable ? `编辑参与者 ${participant.name}` : undefined"
      @click="edit"
      @keydown.enter.prevent="edit"
      @keydown.space.prevent="edit"
      @pointerdown="pressed = true"
      @pointerup="pressed = false"
      @pointercancel="pressed = false"
      @pointerleave="pressed = false"
    >
      <div class="card-body gap-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="card-title">{{ participant.name }}</h3>
              <span
                class="badge badge-sm"
                :class="participant.enabled ? 'badge-success' : 'badge-ghost'"
              >
                {{ participant.enabled ? "启用" : "停用" }}
              </span>
              <span
                class="badge badge-sm"
                :class="participant.is_owner ? 'badge-neutral' : 'badge-ghost'"
              >
                {{ participant.is_owner ? "车主" : "车友" }}
              </span>
              <span class="badge badge-outline badge-sm">
                {{ participant.snapshot?.account_count ?? 0 }} 个已分配账号
              </span>
            </div>
            <p class="mt-1 min-w-0 text-sm opacity-60">
              <span class="break-all">{{
                participant.email || "未填写邮箱"
              }}</span>
              · Sub2API
              <span class="font-medium break-all">{{
                participant.sub2api_identity
              }}</span>
            </p>
          </div>
          <span
            v-if="participant.snapshot"
            class="badge"
            :class="
              participant.snapshot.needs_manual_update
                ? 'badge-warning'
                : participant.snapshot.recommendation_complete
                  ? 'badge-success'
                  : 'badge-ghost'
            "
          >
            {{
              participant.snapshot.needs_manual_update
                ? "建议调整"
                : participant.snapshot.recommendation_complete
                  ? "测算完成"
                  : "等待账号测算"
            }}
          </span>
        </div>

        <div class="grid gap-3 sm:grid-cols-3">
          <div class="rounded-box bg-base-100 p-3">
            <div class="text-xs opacity-60">账号用量合计</div>
            <div class="mt-1 font-semibold tabular-nums">
              {{ formatCurrency(participant.snapshot?.selected_cost) }}
            </div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="text-xs opacity-60">Sub2API 全局余额</div>
            <div class="mt-1 font-semibold tabular-nums">
              {{ formatCurrency(participant.latest_balance_usd) }}
            </div>
          </div>
          <div class="rounded-box bg-base-100 p-3">
            <div class="text-xs opacity-60">全局建议余额</div>
            <div class="mt-1 font-semibold tabular-nums">
              {{
                formatCurrencyRange(
                  participant.snapshot?.recommended_balance_min_usd,
                  participant.snapshot?.recommended_balance_max_usd,
                  participant.snapshot?.recommended_balance_usd,
                )
              }}
            </div>
          </div>
        </div>

        <section
          v-if="sourceGroups.length > 1"
          class="rounded-box border border-base-300 bg-base-100 p-4"
        >
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div class="text-xs font-medium opacity-60">整体权益进度</div>
              <div class="mt-1 font-semibold tabular-nums">
                已使用
                {{
                  formatCurrency(participant.snapshot?.consumed_entitlement_usd)
                }}
                <span class="font-normal opacity-40">/</span>
                预计
                {{
                  formatCurrency(participant.snapshot?.expected_entitlement_usd)
                }}
              </div>
            </div>
            <div class="text-right text-xs tabular-nums opacity-60">
              <div>
                剩余
                {{
                  formatCurrency(
                    participant.snapshot?.remaining_entitlement_usd,
                  )
                }}
              </div>
              <RouterLink
                v-if="editable"
                to="/allocation"
                class="mt-1 inline-block link link-primary"
                @click.stop
              >
                前往分配
              </RouterLink>
            </div>
          </div>
          <EntitlementProgress
            class="mt-3"
            :usage-percent="participant.snapshot?.entitlement_usage_percent"
            :progress-label="`${participant.name}的整体权益进度`"
          />
        </section>

        <section v-if="sourceGroups.length" class="space-y-3">
          <div class="flex items-center justify-between gap-3 px-1">
            <h4 class="text-sm font-semibold">来源明细</h4>
            <span class="text-xs opacity-50">
              {{ sourceGroups.length }} 个额度来源
            </span>
          </div>

          <section
            v-for="group in sourceGroups"
            :key="group.allocation.pool_id"
            class="rounded-box border border-base-300 bg-base-100 p-4"
          >
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <h5 class="truncate font-semibold">{{ group.title }}</h5>
                  <span
                    v-if="group.accounts.length > 1"
                    class="badge badge-ghost badge-xs"
                  >
                    混池
                  </span>
                  <span class="badge badge-outline badge-xs">
                    合同
                    {{ formatCompactPercent(group.allocation.share_percent) }}
                  </span>
                </div>
                <div class="mt-1 text-xs tabular-nums opacity-60">
                  已使用权益 {{ formatCurrency(group.consumedEntitlement) }} ·
                  预计权益 {{ formatCurrency(group.expectedEntitlement) }}
                </div>
              </div>
              <div class="text-right text-xs tabular-nums opacity-60">
                <div v-if="group.accounts.length === 1">
                  账号合计
                  {{ formatCurrency(accountUsage(group.accounts[0])) }}
                </div>
                <div>
                  剩余权益 {{ formatCurrency(group.remainingEntitlement) }}
                </div>
              </div>
            </div>

            <div
              v-if="group.accounts.length > 1"
              class="mt-3 grid gap-x-6 gap-y-2 border-y border-base-300 py-3 sm:grid-cols-2"
            >
              <div
                v-for="account in group.accounts"
                :key="account.account_id"
                class="flex min-w-0 items-center justify-between gap-3"
                :class="{ 'opacity-45': !account.account_enabled }"
              >
                <div class="min-w-0">
                  <div class="flex min-w-0 items-center gap-2">
                    <span class="truncate text-sm font-medium">
                      {{ account.account_name }}
                    </span>
                    <span
                      v-if="!account.account_enabled"
                      class="badge shrink-0 badge-ghost badge-xs"
                    >
                      已停用
                    </span>
                  </div>
                  <div class="mt-0.5 text-[11px] opacity-50">
                    上游 ID {{ account.external_account_id }} · 已归属
                    {{
                      formatCompactPercent(
                        account.snapshot?.charged_cycle_percent,
                      )
                    }}
                  </div>
                </div>
                <div class="shrink-0 text-right text-sm tabular-nums">
                  {{ formatCurrency(accountUsage(account)) }}
                </div>
              </div>
            </div>

            <EntitlementProgress
              class="mt-3"
              :usage-percent="group.usagePercent"
              :progress-label="`${group.title}的权益进度`"
            />
          </section>
        </section>

        <div
          v-else
          class="rounded-box border border-dashed border-base-300 p-4 text-sm opacity-60"
        >
          尚未分配到任何额度池
        </div>

        <div
          class="grid gap-3"
          :class="{ 'sm:grid-cols-2': participant.notes }"
        >
          <div v-if="participant.notes">
            <div class="text-xs opacity-60">备注</div>
            <p class="mt-1 text-sm break-words whitespace-pre-wrap">
              {{ participant.notes }}
            </p>
          </div>
          <div>
            <div class="text-xs opacity-60">额度建议</div>
            <p class="mt-1 text-sm opacity-70">
              {{ participant.snapshot?.reason || "尚无额度测算依据" }}
            </p>
          </div>
        </div>

        <div class="text-xs opacity-50">
          最近探测：{{ dateTime(participant.last_checked_at) }}
        </div>
      </div>
    </article>
  </div>
</template>
