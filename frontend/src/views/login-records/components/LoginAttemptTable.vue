<script setup lang="ts">
import PaginationControls from "@/components/common/PaginationControls.vue";
import { useDateTime } from "@/composables/useDateTime";
import type {
  BlockedIPAddress,
  BlockedIPSource,
  LoginEventRecord,
  PaginationMeta,
} from "@/types";

defineProps<{
  rows: LoginEventRecord[];
  blockedAddresses: BlockedIPAddress[];
  pagination: PaginationMeta;
  loading: boolean;
}>();

const emit = defineEmits<{
  block: [
    address: string,
    sourceType: BlockedIPSource,
    sourceLabel: string,
    eventId: number,
  ];
  page: [page: number];
}>();

const dateTime = useDateTime();

function existingBlock(
  blockedAddresses: BlockedIPAddress[],
  address: string,
  sourceType: BlockedIPSource,
) {
  return blockedAddresses.find(
    (item) => item.address === address && item.source_type === sourceType,
  );
}
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <h2 class="card-title">
        <AppIcon name="finger-print" class="size-5" />最近登录尝试
      </h2>
      <div v-if="loading" class="flex justify-center py-10">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="table min-w-[60rem]">
          <thead>
            <tr>
              <th>时间</th>
              <th>结果</th>
              <th>用户名</th>
              <th>服务端来源 IP</th>
              <th>WebRTC IP</th>
              <th>直连地址</th>
              <th>浏览器</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in rows" :key="item.id">
              <td class="whitespace-nowrap">{{ dateTime(item.created_at) }}</td>
              <td>
                <span
                  class="badge badge-sm"
                  :class="item.success ? 'badge-success' : 'badge-error'"
                >
                  {{ item.success ? "成功" : "失败" }}
                </span>
                <div v-if="item.failure_reason" class="mt-1 text-xs opacity-60">
                  {{ item.failure_reason }}
                </div>
              </td>
              <td>{{ item.username || "—" }}</td>
              <td>
                <button
                  v-if="item.request_ip"
                  class="btn btn-ghost font-mono btn-xs"
                  :disabled="
                    Boolean(
                      existingBlock(
                        blockedAddresses,
                        item.request_ip,
                        'request',
                      ),
                    )
                  "
                  @click="
                    emit(
                      'block',
                      item.request_ip,
                      'request',
                      '服务器来源 IP',
                      item.id,
                    )
                  "
                >
                  {{ item.request_ip }}
                  <span
                    v-if="
                      existingBlock(
                        blockedAddresses,
                        item.request_ip,
                        'request',
                      )
                    "
                    class="badge badge-xs badge-error"
                  >
                    已封禁
                  </span>
                </button>
                <span v-else>—</span>
              </td>
              <td>
                <div v-if="item.webrtc_ips.length" class="flex flex-wrap gap-1">
                  <button
                    v-for="address in item.webrtc_ips"
                    :key="address"
                    class="btn btn-ghost font-mono btn-xs"
                    :disabled="
                      Boolean(
                        existingBlock(blockedAddresses, address, 'webrtc'),
                      )
                    "
                    @click="
                      emit('block', address, 'webrtc', 'WebRTC IP', item.id)
                    "
                  >
                    {{ address }}
                    <span
                      v-if="existingBlock(blockedAddresses, address, 'webrtc')"
                      class="badge badge-xs badge-error"
                    >
                      已封禁
                    </span>
                  </button>
                </div>
                <span v-else class="text-sm opacity-50">
                  {{ item.webrtc_supported ? "未暴露" : "不支持或已禁用" }}
                </span>
              </td>
              <td>
                <button
                  v-if="item.remote_ip"
                  class="btn btn-ghost font-mono btn-xs"
                  :disabled="
                    Boolean(
                      existingBlock(blockedAddresses, item.remote_ip, 'remote'),
                    )
                  "
                  @click="
                    emit('block', item.remote_ip, 'remote', '直连地址', item.id)
                  "
                >
                  {{ item.remote_ip }}
                  <span
                    v-if="
                      existingBlock(blockedAddresses, item.remote_ip, 'remote')
                    "
                    class="badge badge-xs badge-error"
                  >
                    已封禁
                  </span>
                </button>
                <span v-else>—</span>
              </td>
              <td>
                <div
                  class="tooltip tooltip-left"
                  :data-tip="item.user_agent || '—'"
                >
                  <div class="max-w-72 truncate text-xs">
                    {{ item.user_agent || "—" }}
                  </div>
                </div>
              </td>
            </tr>
            <tr v-if="!rows.length">
              <td colspan="7" class="py-8 text-center opacity-60">
                尚无登录记录
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <PaginationControls
        v-if="!loading"
        :page="pagination.page"
        :total-pages="pagination.total_pages"
        :total="pagination.total"
        @change="$emit('page', $event)"
      />
    </div>
  </section>
</template>
