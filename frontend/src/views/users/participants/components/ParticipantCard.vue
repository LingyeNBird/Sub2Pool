<script setup lang="ts">
import { ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";
import type { Participant } from "@/types";
import {
  formatCompactPercent,
  formatCurrency,
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
              <p class="mt-1 text-sm opacity-60">
                {{ participant.email || "未填写邮箱" }} · Sub2API 账号
                <span class="font-medium">{{
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
                class="flex h-8 min-w-0 grow overflow-hidden rounded-box bg-base-300 text-xs font-semibold tabular-nums"
                role="img"
                :aria-label="`已归属 ${formatPercent(participant.snapshot?.charged_cycle_percent)}, 剩余 ${formatPercent(remainingShare())}`"
              >
                <div
                  v-if="(participant.snapshot?.charged_cycle_percent ?? 0) > 0"
                  class="flex items-center justify-center overflow-hidden bg-warning px-1 text-warning-content"
                  :style="{
                    width: `${allocationSegmentWidth(participant.snapshot?.charged_cycle_percent ?? 0)}%`,
                  }"
                >
                  <span class="text-[10px] whitespace-nowrap sm:text-xs">
                    已用
                    {{
                      formatCompactPercent(
                        participant.snapshot?.charged_cycle_percent,
                      )
                    }}
                  </span>
                </div>
                <div
                  v-if="remainingShare() > 0"
                  class="flex items-center justify-center overflow-hidden bg-primary px-1 text-primary-content"
                  :style="{
                    width: `${allocationSegmentWidth(remainingShare())}%`,
                  }"
                >
                  <span class="text-[10px] whitespace-nowrap sm:text-xs">
                    剩余 {{ formatCompactPercent(remainingShare()) }}
                  </span>
                </div>
                <div
                  v-if="
                    (participant.snapshot?.charged_cycle_percent ?? 0) <= 0 &&
                    remainingShare() <= 0
                  "
                  class="flex grow items-center justify-center opacity-60"
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
                    formatCurrency(
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
                <p class="mt-1 text-sm whitespace-pre-wrap">
                  {{ participant.notes }}
                </p>
              </div>
              <div>
                <div class="text-xs opacity-60">额度建议</div>
                <div class="mt-1 flex flex-wrap items-center gap-2">
                  <span
                    class="badge badge-sm"
                    :class="
                      participant.snapshot?.needs_manual_update
                        ? 'badge-warning'
                        : 'badge-success'
                    "
                  >
                    {{
                      !participant.snapshot
                        ? "等待测算"
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
