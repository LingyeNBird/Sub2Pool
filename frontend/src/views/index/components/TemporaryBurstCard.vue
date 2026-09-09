<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import { useDateTime } from "@/composables/useDateTime";
import { api, jsonBody } from "@/services/api";
import type { ConfirmDialogHandle } from "@/types/common";
import type { TemporaryBurstData } from "@/types/temporaryBurst";

const emit = defineEmits<{ changed: [] }>();
const data = ref<TemporaryBurstData | null>(null);
const error = ref("");
const notice = ref("");
const saving = ref(false);
const loading = ref(false);
const confirmation = ref<ConfirmDialogHandle | null>(null);
const dateTime = useDateTime();
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;

async function refresh() {
  if (loading.value || saving.value) return;
  loading.value = true;
  try {
    const next = await api<TemporaryBurstData>("dashboard/temporary-burst");
    if (disposed) return;
    const changed =
      data.value?.active !== undefined && data.value.active !== next.active;
    data.value = next;
    error.value = "";
    if (changed) emit("changed");
  } catch (cause) {
    if (!disposed)
      error.value =
        cause instanceof Error ? cause.message : "临时爽蹬状态读取失败";
  } finally {
    loading.value = false;
  }
}

async function start() {
  if (saving.value || !data.value?.can_start) return;
  if (
    !(await confirmation.value?.open({
      title: "开启临时爽蹬？",
      message: `所有启用的 Sub2API 账号参与本轮，所有已分配参与者的建议余额将设为 9999。${data.value.auto_apply ? "已开启自动应用，将立即尝试写入这些余额。" : "未开启自动应用，需要从额度建议手动应用。"}\n首个账号换周期即统一退出，各账号分别结算借用权益。9999 是美元余额，不是新增订阅容量；高用量可能很快耗尽套餐。`,
      confirmLabel: "确认开启临时爽蹬",
      tone: "warning",
    }))
  )
    return;
  saving.value = true;
  error.value = notice.value = "";
  try {
    data.value = await api<TemporaryBurstData>("dashboard/temporary-burst", {
      method: "POST",
      body: jsonBody({ confirm: true }),
    });
    const result = data.value.application;
    notice.value = data.value.auto_apply
      ? `临时爽蹬已开启；已应用 ${result?.applied ?? 0} 人，失败 ${result?.failed ?? 0} 人。未成功项可从额度建议重试。`
      : "临时爽蹬已开启，建议已切换为 9999；请从额度建议手动应用。";
    emit("changed");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "开启失败";
  } finally {
    saving.value = false;
  }
}

function percent(value: string | number | undefined) {
  return value === undefined ? "—" : `${Number(value).toFixed(2)}%`;
}
function adjustment(value: string | undefined) {
  if (value === undefined) return "—";
  const amount = Number(value);
  return `${amount > 0 ? "+" : ""}${amount.toFixed(2)} 个百分点`;
}
onMounted(() => {
  void refresh();
  timer = setInterval(() => void refresh(), 30000);
});
onBeforeUnmount(() => {
  disposed = true;
  clearInterval(timer);
});
defineExpose({ refresh });
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs" aria-label="临时爽蹬">
    <div class="card-body gap-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="card-title">
          <AppIcon name="bolt" class="size-6 text-primary" />临时爽蹬
        </h2>
        <span
          v-if="data"
          class="badge"
          :class="data.active ? 'badge-warning' : 'badge-ghost'"
          >{{
            data.active ? "本周期生效中" : data.session_id ? "已退出" : "未开启"
          }}</span
        >
      </div>
      <p class="text-sm leading-6 opacity-70">
        临时把所有参与者的建议余额设为
        <strong>9999</strong
        >，按需使用，但不停止记账。超用多少，就在下周期扣多少；补偿按其他人的未用份额分配，未被借用的剩余额度到期作废。
      </p>
      <p v-if="data?.active" class="text-sm">
        最迟退出：{{
          dateTime(data.expires_at)
        }}。任一账号提前换周期也会统一退出，其他账号按各自原周期结束时间结算。
      </p>
      <p
        v-if="data && !data.monitoring_enabled"
        class="alert text-sm alert-warning"
      >
        后台监控已暂停。模式到期后不再建议
        9999，但上游余额回收和权益结算需要恢复监控或手动采样、应用建议。
      </p>
      <p v-if="notice" role="status" class="alert text-sm alert-success">
        {{ notice }}
      </p>
      <p v-if="error" role="alert" class="alert text-sm alert-error">
        {{ error }}
      </p>
      <div class="flex flex-wrap items-center gap-3">
        <button
          type="button"
          class="btn btn-primary btn-sm"
          :disabled="saving || loading || !data?.can_start"
          @click="start"
        >
          <span v-if="saving" class="loading loading-xs loading-spinner"></span
          >{{ data?.active ? "临时爽蹬已开启" : "开启临时爽蹬" }}
        </button>
        <button
          type="button"
          class="btn btn-ghost btn-sm"
          :disabled="loading || saving"
          @click="refresh"
        >
          刷新状态
        </button>
        <RouterLink
          to="/tutorial?page=temporary-burst"
          class="link text-sm link-primary"
          >查看规则与示例</RouterLink
        >
        <span v-if="data" class="text-xs opacity-60">{{
          data.auto_apply ? "自动应用建议已开启" : "需要手动应用建议"
        }}</span>
      </div>
      <p
        v-if="data && !data.active && !data.can_start"
        class="text-sm text-warning"
      >
        仍有账号等待原周期结算；完成后才能开始新一轮。
      </p>
      <details
        v-for="cycle in data?.cycles"
        :key="`${cycle.account_id}-${cycle.resets_at}`"
        class="rounded-box border border-base-300 bg-base-100 p-4"
      >
        <summary class="cursor-pointer text-sm font-medium">
          {{ cycle.account_name }} · {{ dateTime(cycle.resets_at) }} ·
          {{
            cycle.settled_at
              ? "已结算"
              : cycle.is_burst_cycle
                ? "待周期结束"
                : "结转权益生效周期"
          }}
        </summary>
        <p v-if="cycle.error" role="alert" class="mt-3 text-sm text-error">
          {{ cycle.error }}
        </p>
        <div class="mt-3 overflow-x-auto">
          <table class="table table-sm">
            <thead>
              <tr>
                <th>参与者</th>
                <th>合同权益</th>
                <th>本期结转</th>
                <th>本期可用权益</th>
                <th>本期实际归属</th>
                <th>下期调整</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="member in cycle.settlement.length
                  ? cycle.settlement
                  : cycle.members"
                :key="member.participant_id"
              >
                <td>{{ member.name }}</td>
                <td>{{ percent(member.base_share) }}</td>
                <td>{{ adjustment(member.opening_adjustment) }}</td>
                <td>
                  {{
                    percent(
                      member.effective_share ??
                        Number(member.base_share) +
                          Number(member.opening_adjustment),
                    )
                  }}
                </td>
                <td>{{ percent(member.used_percent) }}</td>
                <td>{{ adjustment(member.next_adjustment) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="cycle.evidence_at" class="mt-2 text-xs opacity-60">
          结算依据截至
          {{ dateTime(cycle.evidence_at) }} 的有效观测；不改写合同或真实用量。
        </p>
      </details>
    </div>
    <ConfirmDialog ref="confirmation" />
  </section>
</template>
