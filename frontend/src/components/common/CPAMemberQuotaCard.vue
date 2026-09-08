<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type {
  CPABillingMember,
  CPAMember,
  CPAPoolSummary,
  CPAWeeklyDistribution,
} from "@/types/cpa";
import { cpaColor } from "./cpaColors";
import { formatCurrency } from "@/utils/formatters";

const props = defineProps<{
  member: CPAMember;
  billing?: CPABillingMember;
  accounts: CPAPoolSummary["accounts"];
  selectedAccountId: number;
  weeklyDistributions?: CPAWeeklyDistribution[];
}>();
const selected = ref(props.selectedAccountId);
watch(
  () => props.selectedAccountId,
  (value) => {
    selected.value = value;
  },
);
const breakdown = computed(
  () =>
    props.member.account_breakdowns.find(
      (a) => a.account_id === selected.value,
    ) ?? props.member.account_breakdowns[0],
);
const week = computed(() =>
  props.weeklyDistributions?.find(
    (w) => w.account_id === (breakdown.value?.account_id ?? selected.value),
  ),
);
const budget = computed(() =>
  week.value?.capacity_usd != null && props.member.share_percent != null
    ? (week.value.capacity_usd * props.member.share_percent) / 100
    : (breakdown.value?.expected_entitlement_usd ?? null),
);
const used = computed(
  () =>
    week.value?.members.find(
      (m) => m.participant_id === props.member.participant_id,
    )?.usage_usd ??
    breakdown.value?.usage_usd ??
    props.member.usage_usd,
);
const remaining = computed(() =>
  budget.value == null ? null : budget.value - used.value,
);
const progress = computed(() =>
  budget.value != null && budget.value > 0
    ? (used.value / budget.value) * 100
    : null,
);
const billingRemaining = computed(() =>
  props.billing?.entitlement_usd != null
    ? props.billing.entitlement_usd - props.billing.usage_usd
    : (props.billing?.remaining_usd ?? null),
);
const money = (value: number | null) =>
  value == null ? "待估算" : formatCurrency(value);
const compactTokens = computed(() =>
  new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(props.member.token_count),
);
</script>

<template>
  <article
    class="card min-w-0 bg-base-100 card-border"
    :data-testid="`cpa-member-${member.participant_id}`"
  >
    <div class="card-body gap-4 p-5">
      <header class="flex flex-wrap items-center justify-between gap-2">
        <h3 class="card-title break-all">
          <span
            class="size-3 shrink-0 rounded-full"
            :style="{ backgroundColor: cpaColor(member.participant_id) }"
          />{{ member.participant_name }}
        </h3>
        <div class="flex gap-1">
          <span v-if="member.is_self" class="badge badge-outline badge-sm"
            >我</span
          >
          <span v-if="member.is_owner" class="badge badge-sm badge-neutral"
            >车主</span
          >
          <span
            v-if="remaining != null && remaining < 0"
            class="badge badge-sm badge-warning"
            >本周超预算</span
          >
        </div>
      </header>
      <select
        v-if="member.account_breakdowns.length > 1"
        v-model.number="selected"
        class="select w-full min-w-0 select-sm"
        :aria-label="`${member.participant_name}的额度账号`"
      >
        <option v-for="a in accounts" :key="a.account_id" :value="a.account_id">
          {{ a.account_name }}
        </option>
      </select>
      <div>
        <p class="text-xs text-base-content/60">本周已用</p>
        <p class="mt-1 text-3xl font-semibold tabular-nums">
          {{ formatCurrency(used) }}
        </p>
        <p class="mt-2 text-xs text-base-content/60">
          <span v-if="accounts.length > 1">池内合计 · </span
          >{{ member.request_count.toLocaleString() }} 次请求 ·
          <span :title="`${member.token_count.toLocaleString()} Token`"
            >{{ compactTokens }} Token</span
          >
        </p>
      </div>
      <dl class="grid grid-cols-2 gap-3">
        <div>
          <dt class="text-xs text-base-content/60">
            本周预算<span v-if="member.share_percent != null">
              · {{ member.share_percent.toFixed(2) }}%</span
            >
          </dt>
          <dd class="mt-1 font-semibold tabular-nums">{{ money(budget) }}</dd>
        </div>
        <div>
          <dt class="text-xs text-base-content/60">估算剩余</dt>
          <dd
            class="mt-1 font-semibold tabular-nums"
            :class="remaining != null && remaining < 0 ? 'text-warning' : ''"
          >
            {{ money(remaining) }}
          </dd>
        </div>
      </dl>
      <div v-if="progress != null" class="space-y-2">
        <progress
          class="progress"
          :style="{ color: cpaColor(member.participant_id) }"
          :value="Math.min(100, Math.max(0, progress))"
          max="100"
          :aria-label="`${member.participant_name}已用个人预算 ${progress.toFixed(1)}%`"
        />
        <p class="text-xs text-base-content/60">
          已用个人预算 {{ progress.toFixed(1) }}%
        </p>
      </div>
      <section v-if="billing" class="space-y-3 border-t border-base-300 pt-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h4 class="text-sm font-semibold">账期累计</h4>
          <span
            v-if="billing.usage_percent != null"
            class="text-xs text-base-content/60"
            >占整车 {{ billing.usage_percent.toFixed(1) }}%</span
          >
        </div>
        <strong class="text-xl tabular-nums">{{
          formatCurrency(billing.usage_usd)
        }}</strong>
        <dl
          v-if="billing.entitlement_usd != null || billingRemaining != null"
          class="grid grid-cols-2 gap-3"
        >
          <div v-if="billing.entitlement_usd != null">
            <dt class="text-xs text-base-content/60">预计权益</dt>
            <dd class="mt-1 font-semibold tabular-nums">
              {{ formatCurrency(billing.entitlement_usd) }}
            </dd>
          </div>
          <div v-if="billingRemaining != null">
            <dt class="text-xs text-base-content/60">估算剩余</dt>
            <dd
              class="mt-1 font-semibold tabular-nums"
              :class="billingRemaining < 0 ? 'text-warning' : ''"
            >
              {{ formatCurrency(billingRemaining) }}
            </dd>
          </div>
        </dl>
        <span
          v-if="billingRemaining != null && billingRemaining < 0"
          class="badge badge-sm badge-error"
          >整个账期预计超额</span
        >
        <span
          v-else-if="(billing.completed_overuse_usd ?? 0) > 0"
          class="badge badge-sm badge-warning"
          >已结束周期累计多用
          {{ formatCurrency(billing.completed_overuse_usd) }}</span
        >
        <p
          v-if="billing.recommended_usd != null"
          class="text-xs text-base-content/60"
        >
          后续建议 {{ formatCurrency(billing.recommended_usd)
          }}<span v-if="billing.recommended_percent != null">
            · 占后续可用额度 {{ billing.recommended_percent.toFixed(1) }}%</span
          >
        </p>
      </section>
      <p
        v-if="member.unpriced_request_count"
        class="text-xs text-base-content/70"
      >
        另有 {{ member.unpriced_request_count }} 次请求缺价，未计入已用。
      </p>
    </div>
  </article>
</template>
