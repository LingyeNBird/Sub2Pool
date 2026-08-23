<script setup lang="ts">
import { useDateTime } from "@/composables/useDateTime";
import type { DashboardData } from "@/types/dashboard";

defineProps<{
  data: DashboardData;
}>();

const dateTime = useDateTime();
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
    <div class="card-body">
      <h2 class="card-title">
        <AppIcon name="signal" class="size-5" />采集状态
      </h2>
      <div class="overflow-x-auto">
        <table class="table table-sm">
          <tbody>
            <tr>
              <th>本地用量探测</th>
              <td>{{ dateTime(data.last_local_check_at) }}</td>
            </tr>
            <tr>
              <th>额度快照读取</th>
              <td>
                <span class="inline-flex items-center gap-2">
                  {{ dateTime(data.last_upstream_check_at) }}
                  <span
                    v-if="data.snapshot_stale"
                    class="badge badge-sm badge-warning"
                  >
                    快照陈旧
                  </span>
                </span>
              </td>
            </tr>
            <tr>
              <th>最近成功</th>
              <td>{{ dateTime(data.last_success_at) }}</td>
            </tr>
            <tr>
              <th>运行状态</th>
              <td>{{ data.monitoring_enabled ? "已启用" : "已停用" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
