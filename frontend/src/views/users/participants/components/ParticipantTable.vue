<script setup lang="ts">
import type { Participant } from "@/types";
import {
  formatCurrency,
  formatCurrencyRange,
  formatPercent,
} from "@/utils/formatters";

const props = defineProps<{
  participants: Participant[];
}>();

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
          <th>角色</th>
          <th>权益</th>
          <th>已归属 / 剩余</th>
          <th>账号本周期用量</th>
          <th>当前用户余额</th>
          <th>建议用户余额</th>
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
            <span
              class="badge badge-sm"
              :class="participant.is_owner ? 'badge-neutral' : 'badge-ghost'"
            >
              {{ participant.is_owner ? "车主" : "车友" }}
            </span>
          </td>
          <td>{{ formatPercent(participant.share_percent) }}</td>
          <td>
            {{ formatPercent(participant.snapshot?.charged_cycle_percent) }} /
            {{ formatPercent(participant.snapshot?.remaining_share_percent) }}
          </td>
          <td>{{ formatCurrency(participant.latest_selected_cost) }}</td>
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
              :class="participant.enabled ? 'badge-success' : 'badge-ghost'"
            >
              {{ participant.enabled ? "启用" : "停用" }}
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
