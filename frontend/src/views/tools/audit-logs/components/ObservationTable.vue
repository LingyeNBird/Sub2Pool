<script setup lang="ts">
import PaginationControls from "@/components/common/PaginationControls.vue";
import { useDateTime } from "@/composables/useDateTime";
import type { Observation, PaginationMeta } from "@/types";
import { formatCurrency, formatPercent } from "@/utils/formatters";

import type { ObservationFilterKind, ObservationFilters } from "../types";

defineProps<{
  rows: Observation[];
  loading: boolean;
  pagination: PaginationMeta;
  filters: ObservationFilters;
  restoringId: number | null;
  manualStartId: number | null;
  manualRangeStart: Observation | null;
  rebuilding: boolean;
  fastCorrectionEnabled: boolean;
  fastCorrectionCalculatingIds: Set<number>;
  editable: boolean;
}>();

const emit = defineEmits<{
  filter: [kind: ObservationFilterKind];
  detail: [row: Observation];
  costDetail: [row: Observation];
  fastCorrectionDetail: [row: Observation];
  calculateFastCorrection: [row: Observation];
  exclude: [row: Observation];
  restore: [row: Observation];
  beginManualRange: [row: Observation];
  endManualRange: [row: Observation];
  cancelManualRange: [];
  clearManualStart: [row: Observation];
  pageChange: [page: number];
  rebuild: [];
}>();

const dateTime = useDateTime();

function sourceLabel(value: string) {
  return (
    {
      manual: "手动",
      scheduled: "定时",
      exhausted: "额度耗尽",
      reset: "临近重置",
    }[value] ?? value
  );
}

function isAtOrAfter(row: Observation, start: Observation) {
  const timeDifference =
    Date.parse(row.observed_at) - Date.parse(start.observed_at);
  return timeDifference > 0 || (timeDifference === 0 && row.id >= start.id);
}
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="card-title">
          <AppIcon name="document-magnifying-glass" class="size-5" />校准历史
        </h2>
        <button
          v-if="editable"
          type="button"
          class="btn btn-sm"
          :disabled="rebuilding || loading"
          @click="emit('rebuild')"
        >
          <span
            v-if="rebuilding"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="arrow-path" class="size-4" />
          {{ rebuilding ? "重建中" : "重建计算" }}
        </button>
      </div>
      <div v-if="editable && manualRangeStart" class="alert py-3 alert-info">
        <AppIcon name="arrows-right-left" class="size-5" />
        <div class="grow text-sm">
          <div class="font-medium">已选择开始记录</div>
          <div class="opacity-75">
            {{
              dateTime(manualRangeStart.observed_at)
            }}。请在当前页或翻页后选择同一条或更晚的记录作为结束。
          </div>
        </div>
        <button
          type="button"
          class="btn btn-ghost btn-sm"
          @click="emit('cancelManualRange')"
        >
          取消选择
        </button>
      </div>
      <div v-if="loading" class="flex justify-center py-10">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <template v-else>
        <div class="overflow-x-auto">
          <table class="table min-w-[60rem]">
            <thead>
              <tr>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 text-sm"
                    @click="emit('filter', 'time')"
                  >
                    观测时间
                    <span v-if="filters.from || filters.to" class="text-primary"
                      >●</span
                    >
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 text-sm"
                    @click="emit('filter', 'source')"
                  >
                    来源
                    <span v-if="filters.source" class="text-primary">●</span>
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="btn h-auto min-h-0 btn-ghost p-0 text-sm"
                    @click="emit('filter', 'query')"
                  >
                    查询方式
                    <span v-if="filters.query_mode" class="text-primary"
                      >●</span
                    >
                  </button>
                </th>
                <th>上游已用</th>
                <th>成本增量</th>
                <th v-if="fastCorrectionEnabled">FAST 修正</th>
                <th>百分比增量</th>
                <th>累计样本美元 / 1%</th>
                <th>采用值</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in rows"
                :key="row.id"
                :class="{ 'opacity-55': row.excluded }"
              >
                <td>
                  <div>{{ dateTime(row.observed_at) }}</div>
                  <div class="text-xs opacity-60">
                    快照 {{ dateTime(row.snapshot_sampled_at) }}
                  </div>
                  <div
                    v-if="row.excluded || row.is_manual_start"
                    class="mt-1 flex flex-wrap gap-1"
                  >
                    <template v-if="row.excluded">
                      <span class="badge badge-sm badge-warning">已排除</span>
                      <span class="badge badge-ghost badge-sm">
                        {{
                          row.exclusion_source === "automatic"
                            ? "自动判定"
                            : "管理员"
                        }}
                      </span>
                    </template>
                    <span
                      v-if="row.is_manual_start"
                      class="badge badge-sm badge-primary"
                    >
                      起点区间
                    </span>
                    <span
                      v-if="
                        row.is_manual_start &&
                        row.manual_start_end_observed_at &&
                        row.manual_start_end_id !== row.id
                      "
                      class="badge badge-ghost badge-sm"
                    >
                      至 {{ dateTime(row.manual_start_end_observed_at) }}
                    </span>
                  </div>
                </td>
                <td>
                  <span class="badge badge-ghost badge-sm">{{
                    sourceLabel(row.source)
                  }}</span>
                </td>
                <td>
                  <span
                    class="badge badge-sm"
                    :class="
                      row.query_mode === 'passive'
                        ? 'badge-info'
                        : 'badge-warning'
                    "
                  >
                    {{ row.query_mode === "passive" ? "被动" : "上游直查" }}
                  </span>
                </td>
                <td>{{ formatPercent(row.upstream_used_percent) }}</td>
                <td>
                  <button
                    v-if="row.delta_cost !== null"
                    type="button"
                    class="link cursor-pointer font-medium tabular-nums link-hover"
                    @click="emit('costDetail', row)"
                  >
                    {{ formatCurrency(row.delta_cost) }}
                  </button>
                  <span v-else>—</span>
                </td>
                <td v-if="fastCorrectionEnabled">
                  <button
                    v-if="row.fast_correction_calculated"
                    type="button"
                    class="link cursor-pointer font-medium tabular-nums link-hover"
                    @click="emit('fastCorrectionDetail', row)"
                  >
                    {{ formatCurrency(row.fast_correction_usd) }}
                  </button>
                  <button
                    v-else-if="editable"
                    type="button"
                    class="inline-flex link cursor-pointer items-center gap-1 font-medium link-hover disabled:cursor-wait disabled:opacity-70"
                    :disabled="fastCorrectionCalculatingIds.has(row.id)"
                    title="只计算这一条记录的 FAST 修正"
                    @click="emit('calculateFastCorrection', row)"
                  >
                    <span
                      v-if="fastCorrectionCalculatingIds.has(row.id)"
                      class="loading loading-xs loading-spinner"
                    ></span>
                    {{
                      fastCorrectionCalculatingIds.has(row.id)
                        ? "计算中"
                        : "未计算"
                    }}
                  </button>
                  <span v-else class="opacity-60">未计算</span>
                </td>
                <td>{{ formatPercent(row.delta_percent) }}</td>
                <td>{{ formatCurrency(row.sample_usd_per_percent) }}</td>
                <td class="font-semibold">
                  {{ formatCurrency(row.effective_usd_per_percent) }}
                </td>
                <td>
                  <div class="flex items-center gap-1">
                    <button
                      class="btn btn-ghost btn-xs"
                      @click="emit('detail', row)"
                    >
                      详情
                    </button>
                    <template v-if="editable">
                      <template v-if="manualRangeStart">
                        <button
                          v-if="isAtOrAfter(row, manualRangeStart)"
                          class="btn btn-ghost text-primary btn-xs"
                          @click="emit('endManualRange', row)"
                        >
                          {{
                            row.id === manualRangeStart.id
                              ? "同记录作终点"
                              : "设为终点"
                          }}
                        </button>
                        <span v-else class="px-2 text-xs opacity-45">
                          早于开始
                        </span>
                      </template>
                      <template v-else-if="row.excluded">
                        <button
                          class="btn btn-ghost text-success btn-xs"
                          :disabled="restoringId === row.id"
                          @click="emit('restore', row)"
                        >
                          <span
                            v-if="restoringId === row.id"
                            class="loading loading-xs loading-spinner"
                          ></span>
                          恢复
                        </button>
                      </template>
                      <template v-else>
                        <button
                          class="btn btn-ghost text-primary btn-xs"
                          @click="emit('beginManualRange', row)"
                        >
                          {{ row.is_manual_start ? "调整区间" : "设置区间" }}
                        </button>
                        <button
                          v-if="row.is_manual_start"
                          class="btn btn-ghost text-primary btn-xs"
                          :disabled="manualStartId === row.id"
                          @click="emit('clearManualStart', row)"
                        >
                          <span
                            v-if="manualStartId === row.id"
                            class="loading loading-xs loading-spinner"
                          ></span>
                          取消区间
                        </button>
                        <button
                          class="btn btn-ghost text-warning btn-xs"
                          @click="emit('exclude', row)"
                        >
                          排除
                        </button>
                      </template>
                    </template>
                  </div>
                </td>
              </tr>
              <tr v-if="rows.length === 0">
                <td
                  :colspan="fastCorrectionEnabled ? 10 : 9"
                  class="py-8 text-center opacity-60"
                >
                  尚无观测记录
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <PaginationControls
          :page="pagination.page"
          :total-pages="pagination.total_pages"
          :total="pagination.total"
          @change="emit('pageChange', $event)"
        />
      </template>
    </div>
  </section>
</template>
