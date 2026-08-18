<script setup lang="ts">
import { computed, ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import type { AccountBreakdown, Participant } from "@/types";
import {
  formatCompactPercent,
  formatCurrency,
  formatCurrencyRange,
  formatPercent,
} from "@/utils/formatters";

const props = defineProps<{
  participant: Participant;
  editable: boolean;
}>();

const emit = defineEmits<{
  edit: [participant: Participant];
}>();

const dateTime = useDateTime();
const pressed = ref(false);
const chargedShare = computed(
  () => props.participant.snapshot?.charged_cycle_percent ?? 0,
);
const remainingShare = computed(() =>
  Math.max(0, props.participant.share_percent - chargedShare.value),
);
const allocationTotal = computed(
  () => Math.max(chargedShare.value, 0) + remainingShare.value,
);

function allocationSegmentWidth(value: number) {
  if (allocationTotal.value <= 0) return 0;
  return (Math.max(value, 0) / allocationTotal.value) * 100;
}

function sourceFor(breakdown: AccountBreakdown) {
  return props.participant.snapshot?.sources.find(
    (item) => item.account_id === breakdown.account_id,
  );
}

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
                {{ participant.account_breakdowns.length }} 个账号
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
                  ? "混池完成"
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
            <div class="text-xs opacity-60">混池建议余额</div>
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

        <section class="rounded-box border border-base-300 bg-base-100 p-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div class="text-xs opacity-60">全局混池合同权益</div>
              <div class="mt-1 font-semibold tabular-nums">
                {{ formatPercent(participant.share_percent) }}
              </div>
            </div>
            <div class="text-right text-xs opacity-60">
              已归属 {{ formatCompactPercent(chargedShare) }} · 剩余
              {{ formatCompactPercent(remainingShare) }}
            </div>
          </div>
          <div
            class="relative mt-3 flex h-7 min-w-0 overflow-hidden rounded-box bg-base-300 text-xs font-semibold tabular-nums"
            role="img"
            :aria-label="`全局混池合同：已归属 ${formatPercent(chargedShare)}，剩余 ${formatPercent(remainingShare)}`"
          >
            <div class="flex h-full w-full" aria-hidden="true">
              <div
                v-if="chargedShare > 0"
                class="h-full shrink-0 bg-warning"
                :style="{ width: `${allocationSegmentWidth(chargedShare)}%` }"
              ></div>
              <div
                v-if="remainingShare > 0"
                class="h-full shrink-0 bg-primary"
                :style="{ width: `${allocationSegmentWidth(remainingShare)}%` }"
              ></div>
            </div>
            <span
              v-if="chargedShare > 0"
              class="pointer-events-none absolute inset-y-0 left-0 z-10 flex max-w-full min-w-fit items-center justify-center px-1 text-[10px] whitespace-nowrap text-white [text-shadow:0_1px_2px_rgb(0_0_0/0.8)] sm:text-xs"
              :style="{ width: `${allocationSegmentWidth(chargedShare)}%` }"
            >
              已归属 {{ formatCompactPercent(chargedShare) }}
            </span>
            <span
              v-if="remainingShare > 0"
              class="pointer-events-none absolute inset-y-0 right-0 z-10 flex max-w-full min-w-fit items-center justify-center px-1 text-[10px] whitespace-nowrap text-white [text-shadow:0_1px_2px_rgb(0_0_0/0.8)] sm:text-xs"
              :style="{ width: `${allocationSegmentWidth(remainingShare)}%` }"
            >
              剩余 {{ formatCompactPercent(remainingShare) }}
            </span>
            <div
              v-if="chargedShare <= 0 && remainingShare <= 0"
              class="absolute inset-0 flex items-center justify-center opacity-60"
            >
              暂无可分配权益
            </div>
          </div>
        </section>

        <div class="space-y-2">
          <div
            v-for="breakdown in participant.account_breakdowns"
            :key="breakdown.account_id"
            class="rounded-box border border-base-300 bg-base-100 px-3 py-3"
            :class="{ 'opacity-50': !breakdown.account_enabled }"
          >
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-medium">{{ breakdown.account_name }}</span>
                  <span
                    v-if="!breakdown.account_enabled"
                    class="badge badge-ghost badge-xs"
                  >
                    账号停用
                  </span>
                </div>
                <div class="mt-1 text-xs opacity-60">
                  当账号归属
                  {{
                    formatCompactPercent(
                      breakdown.snapshot?.charged_cycle_percent,
                    )
                  }}
                  · 用量 {{ formatCurrency(breakdown.latest_selected_cost) }}
                </div>
              </div>
              <div class="text-right text-sm tabular-nums">
                <div>
                  混池贡献
                  {{ formatCurrency(sourceFor(breakdown)?.contribution_usd) }}
                </div>
                <div class="text-xs opacity-60">
                  净权益
                  {{ formatCurrency(sourceFor(breakdown)?.net_position_usd) }}
                </div>
              </div>
            </div>
          </div>
          <div
            v-if="!participant.account_breakdowns.length"
            class="rounded-box border border-dashed border-base-300 p-3 text-sm opacity-60"
          >
            尚未添加监控账号
          </div>
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
              {{ participant.snapshot?.reason || "尚无混池测算依据" }}
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
