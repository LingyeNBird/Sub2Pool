<script setup lang="ts">
import { useDateTime } from "@/composables/useDateTime";
import type { BlockedIPAddress } from "@/types/security";

defineProps<{
  blockedAddresses: BlockedIPAddress[];
  editable: boolean;
}>();

defineEmits<{
  unblock: [item: BlockedIPAddress];
}>();

const dateTime = useDateTime();
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <div>
        <h2 class="card-title">
          <AppIcon name="no-symbol" class="size-5" />已封禁列表
        </h2>
        <p class="mt-1 text-sm opacity-60">
          所有可识别的封禁地址都不会获得页面或响应正文；WebRTC
          地址在浏览器完成自报后生效，因此首次登录页面仍可能已经传输。
        </p>
      </div>
      <div v-if="blockedAddresses.length" class="overflow-x-auto">
        <table class="table table-sm">
          <thead>
            <tr>
              <th>类型</th>
              <th>地址</th>
              <th>备注</th>
              <th>封禁时间</th>
              <th v-if="editable"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in blockedAddresses" :key="item.id">
              <td>
                <span class="badge badge-ghost badge-sm">
                  {{ item.source_label }}
                </span>
              </td>
              <td class="font-mono text-xs">{{ item.address }}</td>
              <td>{{ item.notes || "—" }}</td>
              <td class="whitespace-nowrap">{{ dateTime(item.created_at) }}</td>
              <td v-if="editable" class="text-right">
                <button
                  class="btn btn-ghost text-error btn-xs"
                  @click="$emit('unblock', item)"
                >
                  解除
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="py-4 text-center text-sm opacity-60">
        当前没有已封禁地址
      </div>
    </div>
  </section>
</template>
