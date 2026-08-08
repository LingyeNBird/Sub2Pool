<script setup lang="ts">
import PaginationControls from "@/components/common/PaginationControls.vue";
import { useDateTime } from "@/composables/useDateTime";
import type { NotificationRecord, PaginationMeta } from "@/types";

import type { NotificationFilterKind, NotificationFilters } from "../types";

defineProps<{
  rows: NotificationRecord[];
  filters: NotificationFilters;
  pagination: PaginationMeta;
  loading: boolean;
}>();

defineEmits<{
  filter: [kind: NotificationFilterKind];
  detail: [row: NotificationRecord];
  page: [page: number];
}>();

const dateTime = useDateTime();
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <h2 class="card-title">
        <AppIcon name="envelope" class="size-5" />邮件审计
      </h2>
      <div v-if="loading" class="flex justify-center py-10">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <template v-else>
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 text-sm"
                    @click="$emit('filter', 'time')"
                  >
                    时间
                    <span v-if="filters.from || filters.to" class="text-primary"
                      >●</span
                    >
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 text-sm"
                    @click="$emit('filter', 'type')"
                  >
                    类型
                    <span v-if="filters.event_type" class="text-primary"
                      >●</span
                    >
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 text-sm"
                    @click="$emit('filter', 'participant')"
                  >
                    参与者
                    <span v-if="filters.participant" class="text-primary"
                      >●</span
                    >
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 text-sm"
                    @click="$emit('filter', 'subject')"
                  >
                    主题
                    <span v-if="filters.subject" class="text-primary">●</span>
                  </button>
                </th>
                <th>收件人</th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 text-sm"
                    @click="$emit('filter', 'status')"
                  >
                    状态
                    <span v-if="filters.status" class="text-primary">●</span>
                  </button>
                </th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in rows" :key="row.id">
                <td>{{ dateTime(row.created_at) }}</td>
                <td>{{ row.event_type_label }}</td>
                <td>{{ row.participant_name || "系统" }}</td>
                <td>{{ row.subject }}</td>
                <td>{{ row.recipient || "未配置" }}</td>
                <td>
                  <span
                    class="badge badge-sm"
                    :class="
                      row.status === 'sent'
                        ? 'badge-success'
                        : row.status === 'failed'
                          ? 'badge-error'
                          : 'badge-warning'
                    "
                  >
                    {{ row.status_label }}
                  </span>
                </td>
                <td>
                  <button
                    class="btn btn-ghost btn-xs"
                    @click="$emit('detail', row)"
                  >
                    详情
                  </button>
                </td>
              </tr>
              <tr v-if="rows.length === 0">
                <td colspan="7" class="py-8 text-center opacity-60">
                  尚无通知记录
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <PaginationControls
          :page="pagination.page"
          :total-pages="pagination.total_pages"
          :total="pagination.total"
          @change="$emit('page', $event)"
        />
      </template>
    </div>
  </section>
</template>
