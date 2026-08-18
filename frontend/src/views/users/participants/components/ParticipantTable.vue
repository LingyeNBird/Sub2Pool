<script setup lang="ts">
import type { Participant } from "@/types";
import {
  formatCurrency,
  formatCurrencyRange,
  formatPercent,
} from "@/utils/formatters";

const props = defineProps<{ participants: Participant[] }>();

defineEmits<{
  edit: [participant: Participant];
  remove: [participant: Participant];
}>();
</script>

<template>
  <div class="overflow-x-auto rounded-box bg-base-200 shadow-xs">
    <table class="table">
      <thead>
        <tr>
          <th>参与者</th>
          <th>混池权益</th>
          <th>账号用量合计</th>
          <th>全局余额</th>
          <th>聚合建议</th>
          <th>状态</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="participant in props.participants" :key="participant.id">
          <td>
            <div class="font-bold">{{ participant.name }}</div>
            <div class="text-sm opacity-60">
              {{
                participant.email || `用户 ID ${participant.sub2api_user_id}`
              }}
            </div>
          </td>
          <td>
            <div class="flex flex-wrap items-center gap-1">
              <span class="font-semibold tabular-nums">
                {{ formatPercent(participant.share_percent) }}
              </span>
              <span
                class="badge badge-xs"
                :class="participant.is_owner ? 'badge-neutral' : 'badge-ghost'"
              >
                {{ participant.is_owner ? "车主" : "车友" }}
              </span>
            </div>
          </td>
          <td>{{ formatCurrency(participant.snapshot?.selected_cost) }}</td>
          <td>{{ formatCurrency(participant.latest_balance_usd) }}</td>
          <td class="font-semibold">
            {{
              formatCurrencyRange(
                participant.snapshot?.recommended_balance_min_usd,
                participant.snapshot?.recommended_balance_max_usd,
                participant.snapshot?.recommended_balance_usd,
              )
            }}
          </td>
          <td>
            <span
              class="badge badge-sm"
              :class="
                !participant.enabled
                  ? 'badge-ghost'
                  : participant.snapshot?.needs_manual_update
                    ? 'badge-warning'
                    : participant.snapshot?.recommendation_complete
                      ? 'badge-success'
                      : 'badge-ghost'
              "
            >
              {{
                !participant.enabled
                  ? "停用"
                  : participant.snapshot?.needs_manual_update
                    ? "建议调整"
                    : participant.snapshot?.recommendation_complete
                      ? "正常"
                      : "等待测算"
              }}
            </span>
          </td>
          <td class="text-right">
            <button
              class="btn btn-ghost btn-xs"
              @click="$emit('edit', participant)"
            >
              编辑
            </button>
            <button
              class="btn btn-ghost text-error btn-xs"
              @click="$emit('remove', participant)"
            >
              删除
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
