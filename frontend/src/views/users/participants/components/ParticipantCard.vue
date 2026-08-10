<script setup lang="ts">
import { ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import type { Participant } from "@/types";
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

function remainingShare() {
  return (
    props.participant.snapshot?.remaining_share_percent ??
    props.participant.share_percent
  );
}

function allocationSegmentWidth(value: number) {
  const used = props.participant.snapshot?.charged_cycle_percent ?? 0;
  const remaining = remainingShare();
  const total = Math.max(used, 0) + Math.max(remaining, 0);
  if (total === 0) return 0;
  return (Math.max(value, 0) / total) * 100;
}

function edit() {
  if (props.editable) emit("edit", props.participant);
}
</script>

<template>
  <div class="relative min-w-0 p-3">
    <div
      class="w-full"
      :class="[
        editable
          ? 'cursor-pointer transition-transform duration-150 select-none'
          : 'cursor-default',
        { 'scale-95': editable && pressed },
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
      <div class="hover-3d w-full">
        <article class="card w-full bg-base-200 shadow-xs">
          <div class="card-body gap-4">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="card-title">{{ participant.name }}</h3>
                <span
                  class="badge badge-sm"
                  :class="
                    participant.is_owner ? 'badge-neutral' : 'badge-ghost'
                  "
                >
                  {{ participant.is_owner ? "车主" : "车友" }}
                </span>
                <span
                  class="badge badge-sm"
                  :class="participant.enabled ? 'badge-success' : 'badge-ghost'"
                >
                  {{ participant.enabled ? "启用" : "停用" }}
                </span>
              </div>
              <p class="mt-1 min-w-0 text-sm opacity-60">
                <span class="break-all">{{
                  participant.email || "未填写邮箱"
                }}</span>
                · Sub2API 账号
                <span class="font-medium break-all">{{
                  participant.sub2api_identity
                }}</span>
              </p>
            </div>

            <div
              class="rounded-box bg-base-100 p-3 sm:flex sm:items-center sm:gap-5"
            >
              <div class="mb-3 shrink-0 sm:mb-0 sm:min-w-24">
                <div class="text-xs opacity-60">合同权益</div>
                <div class="mt-1 text-xl font-semibold tabular-nums">
                  {{ formatPercent(participant.share_percent) }}
                </div>
              </div>
              <div
                class="relative flex h-8 min-w-0 grow overflow-hidden rounded-box bg-base-300 text-xs font-semibold tabular-nums"
                role="img"
                :aria-label="`已归属 ${formatPercent(participant.snapshot?.charged_cycle_percent)}, 剩余 ${formatPercent(remainingShare())}`"
              >
                <div class="flex h-full w-full" aria-hidden="true">
                  <div
                    v-if="
                      (participant.snapshot?.charged_cycle_percent ?? 0) > 0
                    "
                    class="h-full shrink-0 bg-warning"
                    :style="{
                      width: `${allocationSegmentWidth(participant.snapshot?.charged_cycle_percent ?? 0)}%`,
                    }"
                  ></div>
                  <div
                    v-if="remainingShare() > 0"
                    class="h-full shrink-0 bg-primary"
                    :style="{
                      width: `${allocationSegmentWidth(remainingShare())}%`,
                    }"
                  ></div>
                </div>
                <span
                  v-if="(participant.snapshot?.charged_cycle_percent ?? 0) > 0"
                  class="pointer-events-none absolute inset-y-0 left-0 z-10 flex max-w-full min-w-fit items-center justify-center px-1 text-[10px] whitespace-nowrap text-white [text-shadow:0_1px_2px_rgb(0_0_0/0.8)] sm:text-xs"
                  :style="{
                    width: `${allocationSegmentWidth(participant.snapshot?.charged_cycle_percent ?? 0)}%`,
                  }"
                >
                  已用
                  {{
                    formatCompactPercent(
                      participant.snapshot?.charged_cycle_percent,
                    )
                  }}
                </span>
                <span
                  v-if="remainingShare() > 0"
                  class="pointer-events-none absolute inset-y-0 right-0 z-10 flex max-w-full min-w-fit items-center justify-center px-1 text-[10px] whitespace-nowrap text-white [text-shadow:0_1px_2px_rgb(0_0_0/0.8)] sm:text-xs"
                  :style="{
                    width: `${allocationSegmentWidth(remainingShare())}%`,
                  }"
                >
                  剩余 {{ formatCompactPercent(remainingShare()) }}
                </span>
                <div
                  v-if="
                    (participant.snapshot?.charged_cycle_percent ?? 0) <= 0 &&
                    remainingShare() <= 0
                  "
                  class="absolute inset-0 flex items-center justify-center opacity-60"
                >
                  暂无可分配权益
                </div>
              </div>
            </div>

            <div class="grid gap-3 sm:grid-cols-2">
              <div class="rounded-box bg-base-100 p-3">
                <div class="text-xs opacity-60">账号本周期用量</div>
                <div class="mt-1 font-semibold tabular-nums">
                  {{ formatCurrency(participant.latest_selected_cost) }}
                </div>
              </div>
              <div class="rounded-box bg-base-100 p-3">
                <div class="text-xs opacity-60">余额</div>
                <div class="mt-1 font-semibold tabular-nums">
                  当前 {{ formatCurrency(participant.latest_balance_usd) }} /
                  建议
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
                <div class="mt-1 flex flex-wrap items-center gap-2">
                  <span
                    class="badge badge-sm"
                    :class="
                      participant.snapshot?.is_overused
                        ? 'badge-error'
                        : participant.snapshot?.needs_manual_update
                          ? 'badge-warning'
                          : 'badge-success'
                    "
                  >
                    {{
                      !participant.snapshot
                        ? "等待测算"
                        : participant.snapshot.is_overused
                          ? `已超用 ${formatCompactPercent(participant.snapshot.overused_percent)}`
                          : participant.snapshot.needs_manual_update
                            ? "建议调整"
                            : "无需调整"
                    }}
                  </span>
                  <span class="text-sm opacity-60">
                    {{ participant.snapshot?.reason || "尚无测算依据" }}
                  </span>
                </div>
              </div>
            </div>

            <div class="text-xs opacity-50">
              最近探测：{{ dateTime(participant.last_checked_at) }}
            </div>
          </div>
        </article>
        <div></div>
        <div></div>
        <div></div>
        <div></div>
        <div></div>
        <div></div>
        <div></div>
        <div></div>
      </div>
    </div>
  </div>
</template>
