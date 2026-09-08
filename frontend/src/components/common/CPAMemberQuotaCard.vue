<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { CPABillingMember, CPAMember, CPAPoolSummary } from "@/types/cpa";
import { cpaColor } from "./cpaColors";
import { formatCurrency } from "@/utils/formatters";

const props = defineProps<{
  member: CPAMember;
  billing?: CPABillingMember;
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
const trueProgress = computed(() =>
  props.member.share_percent
    ? ((breakdown.value?.charged_percent ?? 0) / props.member.share_percent) *
      100
    : null,
);
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
          <span v-if="member.is_overused" class="badge badge-sm badge-warning"
            >已超额</span
          >
        </div>
      </header>
      <div>
        <p class="text-xs text-base-content/60">本周已采集 · 估算</p>
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
          :aria-label="`${member.participant_name}已使用其合同份额的 ${trueProgress?.toFixed(1) ?? '未知'}%`"
        ></progress>
        <p class="text-xs text-base-content/60">
          进度以个人份额为 100%，实际已用
          {{ trueProgress == null ? "未知" : `${trueProgress.toFixed(1)}%` }}。
        </p>
      </template>
      <section v-if="billing" class="space-y-3 border-t border-base-300 pt-4">
        <div class="flex items-center justify-between gap-2">
          <h4 class="text-sm font-semibold">账期累计 · 估算</h4>
          <span class="text-xs text-base-content/60"
            >占整车
            {{
              billing.usage_percent == null
                ? "未知"
                : `${billing.usage_percent.toFixed(1)}%`
            }}</span
          >
        </div>
        <div class="flex flex-wrap justify-between gap-2">
          <strong class="text-xl">{{
            formatCurrency(billing.usage_usd)
          }}</strong
          ><span class="text-sm"
            >预计剩余权益
            {{
              billing.remaining_usd == null
                ? "未知"
                : formatCurrency(billing.remaining_usd)
            }}</span
          >
        </div>
        <p class="text-xs text-base-content/60">
          账期预计权益
          {{
            billing.entitlement_usd == null
              ? "未知"
              : formatCurrency(billing.entitlement_usd)
          }}
        </p>
        <span
          v-if="billing.projected_overuse"
          class="badge badge-sm badge-error"
          >整个账期预计超额</span
        >
        <span
          v-else-if="(billing.completed_overuse_usd ?? 0) > 0"
          class="badge badge-sm badge-warning"
          >已结束周期累计多用
          {{ formatCurrency(billing.completed_overuse_usd) }}</span
        >
        <p class="rounded-box bg-base-200 p-3 text-sm">
          后续建议总量
          {{
            billing.recommended_usd == null
              ? "数据不足"
              : formatCurrency(billing.recommended_usd)
          }}<span v-if="billing.recommended_percent != null"
            >，约占后续可用额度的
            {{ billing.recommended_percent.toFixed(1) }}%</span
          >。<span
            v-if="billing.completed_overuse_usd && !billing.projected_overuse"
            >后续少用一些，可在账期内平衡。</span
          >
        </p>
      </section>
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
        本周超份额，当前仍可调用；不直接判定账期超额。
      </p>
    </div>
  </article>
</template>
