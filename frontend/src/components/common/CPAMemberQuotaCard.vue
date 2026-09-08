<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { CPAMember, CPAPoolSummary } from "@/types/cpa";
import { formatCurrency } from "@/utils/formatters";

const props = defineProps<{
  member: CPAMember;
  accounts: CPAPoolSummary["accounts"];
  selectedAccountId: number;
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
const account = computed(() =>
  props.accounts.find((a) => a.account_id === breakdown.value?.account_id),
);
const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${value.toFixed(2)}%`;
const progressValue = computed(() => {
  if (!breakdown.value?.quota_available) return 0;
  if (!props.member.share_percent)
    return (breakdown.value.charged_percent ?? 0) > 0 ? 100 : 0;
  return Math.min(
    100,
    Math.max(
      0,
      ((breakdown.value.charged_percent ?? 0) / props.member.share_percent) *
        100,
    ),
  );
});
const reasons = computed(() =>
  (breakdown.value?.quota_unavailable_reasons ?? []).filter(
    (reason) => !account.value?.quota_unavailable_reasons?.includes(reason),
  ),
);
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
        <h3 class="card-title break-all">{{ member.participant_name }}</h3>
        <div class="flex gap-1">
          <span v-if="member.is_self" class="badge badge-outline badge-sm"
            >我</span
          >
          <span v-if="member.is_owner" class="badge badge-sm badge-neutral"
            >车主</span
          >
          <span v-if="member.is_overused" class="badge badge-sm badge-warning"
            >已超额</span
          >
        </div>
      </header>
      <div>
        <p class="text-xs text-base-content/60">已采集消耗 · 估算</p>
        <p class="mt-1 text-3xl font-semibold tabular-nums">
          {{ formatCurrency(member.usage_usd) }}
        </p>
        <p class="mt-2 text-xs text-base-content/60">
          {{ member.request_count.toLocaleString() }} 次请求 ·
          <span :title="`${member.token_count.toLocaleString()} Token`"
            >{{ compactTokens }} Token</span
          >
        </p>
      </div>
      <dl class="grid grid-cols-2 gap-3 border-t border-base-300 pt-4">
        <div>
          <dt class="text-xs text-base-content/60">分配份额</dt>
          <dd class="mt-1 font-semibold tabular-nums">
            {{ percent(member.share_percent) }}
          </dd>
        </div>
        <div>
          <dt class="text-xs text-base-content/60">
            {{ accounts.length > 1 ? "池内剩余 · 估算" : "剩余额度 · 估算" }}
          </dt>
          <dd class="mt-1 font-semibold tabular-nums">
            {{
              member.quota_available
                ? formatCurrency(member.remaining_entitlement_usd)
                : "待估算"
            }}
          </dd>
        </div>
      </dl>
      <p v-if="member.quota_available" class="text-xs text-base-content/60">
        已用权益 {{ formatCurrency(member.consumed_entitlement_usd) }} / 总额
        {{ formatCurrency(member.expected_entitlement_usd) }}
      </p>
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
      <template v-if="breakdown?.quota_available">
        <div class="flex flex-wrap justify-between gap-2 text-xs">
          <span>已用 {{ percent(breakdown.charged_percent) }}</span>
          <span>剩余份额 {{ percent(breakdown.remaining_share_percent) }}</span>
        </div>
        <progress
          class="progress progress-primary"
          :value="progressValue"
          max="100"
          :aria-label="`${member.participant_name}已使用其合同份额的 ${progressValue.toFixed(1)}%`"
        ></progress>
        <p class="text-xs text-base-content/60">
          进度以该成员合同份额为 100%。
        </p>
      </template>
      <p v-if="reasons.length" class="text-xs leading-5 text-base-content/70">
        {{ reasons.join("；") }}
      </p>
      <p
        v-if="member.unpriced_request_count"
        class="text-xs text-base-content/70"
      >
        另有 {{ member.unpriced_request_count }} 次请求缺价，未计入消耗。
      </p>
      <p v-if="member.is_overused" class="text-sm">
        已超出分配额度，当前仍可调用。
      </p>
    </div>
  </article>
</template>
