<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { CPAMember, CPAPoolSummary } from "@/types/cpa";
import { formatCurrency } from "@/utils/formatters";
import { useDateTime } from "@/composables/useDateTime";

const props = defineProps<{
  member: CPAMember;
  accounts: CPAPoolSummary["accounts"];
  selectedAccountId: number;
}>();
const formatTime = useDateTime();
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
  breakdown.value?.quota_unavailable_reasons?.length
    ? breakdown.value.quota_unavailable_reasons
    : ["等待有效观测、完整采集区间和历史份额依据"],
);
</script>

<template>
  <article
    class="card min-w-0 bg-base-100 card-border"
    :data-testid="`cpa-member-${member.participant_id}`"
  >
    <div class="card-body gap-5">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h3 class="card-title break-all">
          {{ member.participant_name }}的额度
        </h3>
        <div class="flex gap-2">
          <span v-if="member.is_self" class="badge badge-outline">我</span
          ><span v-else class="badge badge-ghost">同车成员</span
          ><span v-if="member.is_owner" class="badge badge-neutral">车主</span
          ><span v-if="member.is_overused" class="badge badge-warning"
            >已超额</span
          >
        </div>
      </div>
      <div>
        <p class="text-xs text-base-content/60">
          {{
            accounts.length > 1 ? "池内剩余额度（估算）" : "剩余额度（估算）"
          }}
        </p>
        <p class="mt-2 text-3xl font-semibold tabular-nums">
          {{
            member.quota_available
              ? formatCurrency(member.remaining_entitlement_usd)
              : "数据不足"
          }}
        </p>
        <p
          v-if="member.quota_available"
          class="mt-2 text-xs text-base-content/60"
        >
          已用权益 {{ formatCurrency(member.consumed_entitlement_usd) }} /
          权益总额 {{ formatCurrency(member.expected_entitlement_usd) }}
        </p>
        <p v-else class="mt-2 text-xs leading-5 text-base-content/60">
          已采集消耗可查看，完整剩余额度尚无法可靠估算。
        </p>
      </div>
      <div class="rounded-box border border-base-300 bg-base-200 p-4">
        <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
          <select
            v-if="member.account_breakdowns.length > 1"
            v-model.number="selected"
            class="select min-w-0 flex-1 select-sm"
            :aria-label="`${member.participant_name}的额度账号`"
          >
            <option
              v-for="a in accounts"
              :key="a.account_id"
              :value="a.account_id"
            >
              {{ a.account_name }}
            </option>
          </select>
          <h4 v-else class="min-w-0 text-sm font-medium break-all">
            {{ account?.account_name || "CPA 账号" }}
          </h4>
          <span class="badge badge-outline badge-sm">{{
            member.share_percent == null
              ? "未分配份额"
              : `合同 ${percent(member.share_percent)}`
          }}</span>
        </div>
        <template v-if="breakdown?.quota_available">
          <div class="mb-2 grid grid-cols-2 gap-3 text-sm tabular-nums">
            <div>
              <p class="text-xs text-base-content/60">已使用</p>
              <p class="mt-1 font-semibold">
                {{ percent(breakdown.charged_percent) }}
              </p>
              <p class="text-xs">
                {{ formatCurrency(breakdown.consumed_entitlement_usd) }}
              </p>
            </div>
            <div class="text-right">
              <p class="text-xs text-base-content/60">剩余份额</p>
              <p class="mt-1 font-semibold">
                {{ percent(breakdown.remaining_share_percent) }}
              </p>
              <p class="text-xs">
                {{ formatCurrency(breakdown.remaining_entitlement_usd) }}
              </p>
            </div>
          </div>
          <progress
            class="progress h-4 bg-primary progress-warning"
            :value="progressValue"
            max="100"
            :aria-label="`${member.participant_name}已使用其合同份额的 ${progressValue.toFixed(1)}%`"
          ></progress>
          <p class="mt-2 text-xs text-base-content/60">
            进度条以该成员合同份额为 100%。
          </p>
        </template>
        <div
          v-else
          class="rounded-box border border-dashed border-base-content/20 p-3"
          role="status"
        >
          <p class="flex items-center gap-2 text-sm font-medium">
            <AppIcon name="clock" class="size-4 shrink-0" />剩余份额待估算
          </p>
          <ul class="mt-2 space-y-1 text-xs leading-5 text-base-content/70">
            <li v-for="reason in reasons" :key="reason">{{ reason }}</li>
          </ul>
        </div>
        <p
          v-if="account?.quota_as_of"
          class="mt-3 text-xs text-base-content/60"
        >
          额度更新 {{ formatTime(account.quota_as_of) }}
        </p>
      </div>
      <div
        class="flex flex-wrap items-end justify-between gap-3 border-t border-base-300 pt-4"
      >
        <div>
          <p class="text-xs text-base-content/60">已采集消耗</p>
          <p class="mt-1 text-xl font-semibold tabular-nums">
            {{ formatCurrency(member.usage_usd)
            }}<span class="ml-2 text-xs font-normal text-base-content/60"
              >估算</span
            >
          </p>
        </div>
        <p class="text-xs text-base-content/60">
          {{ member.request_count.toLocaleString() }} 次请求 ·
          {{ member.token_count.toLocaleString() }} Token
        </p>
      </div>
      <p v-if="member.unpriced_request_count" class="text-xs">
        另有 {{ member.unpriced_request_count }} 次请求未计价，未计入上述消耗。
      </p>
      <p v-if="member.is_overused" class="text-sm">
        已超出分配额度，当前仍可调用。
      </p>
    </div>
  </article>
</template>
