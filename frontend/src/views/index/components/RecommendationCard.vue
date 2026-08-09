<script setup lang="ts">
import type { Participant } from "@/types";
import { formatCurrency, formatCurrencyRange } from "@/utils/formatters";

defineProps<{
  participant: Participant;
  applied: boolean;
}>();

defineEmits<{
  select: [participant: Participant];
}>();
</script>

<template>
  <button
    type="button"
    class="relative w-full min-w-0 rounded-box border border-base-300 bg-base-100 p-5 text-left"
    :class="applied ? 'cursor-default' : 'cursor-pointer'"
    :disabled="applied"
    :aria-label="`处理参与者 ${participant.name} 的额度建议`"
    @click="$emit('select', participant)"
  >
    <AppIcon
      v-if="applied"
      name="check-circle"
      class="absolute top-1/2 left-6 z-10 size-14 -translate-y-1/2 text-success"
    />
    <div :class="{ 'blur-sm': applied }">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <p class="min-w-0 text-lg leading-9 font-semibold sm:text-xl">
          对于参与者
          <strong class="text-xl break-words sm:text-2xl">{{
            participant.name
          }}</strong>
          （Sub2API 账号
          <span class="font-bold break-all">{{
            participant.sub2api_identity
          }}</span
          >），
          <template v-if="participant.snapshot">
            建议把 Sub2API 用户余额设置为
            <strong class="text-2xl font-bold text-primary sm:text-3xl">
              {{
                formatCurrencyRange(
                  participant.snapshot.recommended_balance_min_usd,
                  participant.snapshot.recommended_balance_max_usd,
                  participant.snapshot.recommended_balance_usd,
                )
              }}
            </strong>
            。
          </template>
          <template v-else>尚无额度建议，请先完成一次有效测算。</template>
        </p>
        <span
          class="badge"
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
                ? "建议手动调整"
                : "当前无需调整"
          }}
        </span>
      </div>
      <p v-if="participant.snapshot" class="mt-3 text-sm opacity-60">
        该参与者本周期用量为
        {{ formatCurrency(participant.latest_selected_cost) }}，当前余额为
        {{ formatCurrency(participant.latest_balance_usd) }}，{{
          participant.snapshot.needs_manual_update
            ? "和建议余额差异较大。"
            : "与建议余额的差异未达到调整阈值。"
        }}
      </p>
      <p v-else class="mt-3 text-sm opacity-60">该参与者尚无本周期测算数据。</p>
    </div>
  </button>
</template>
