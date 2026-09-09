<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import { useDateTime } from "@/composables/useDateTime";
import { api, jsonBody } from "@/services/api";
import type { ConfirmDialogHandle } from "@/types/common";
import type { TemporaryBurstData } from "@/types/temporaryBurst";
import { temporaryBurstState } from "@/stores/temporaryBurst";

const emit = defineEmits<{ changed: [] }>();
const data = temporaryBurstState;
const error = ref("");
const notice = ref("");
const saving = ref(false);
const loading = ref(false);
const confirmation = ref<ConfirmDialogHandle | null>(null);
const dateTime = useDateTime();
let disposed = false;

async function toggleReminder() {
  if (!data.value || saving.value) return;
  saving.value = true;
  error.value = "";
  try {
    data.value = await api<TemporaryBurstData>("dashboard/temporary-burst", {
      method: "PATCH",
      body: jsonBody({
        session_id: data.value.session_id,
        reminder_enabled: !data.value.reminder_enabled,
      }),
    });
    notice.value = data.value.reminder_enabled
      ? "本轮用满提醒已开启：账号达到 95% 后，每半小时最多邮件提醒管理员一次；换周期后停止该账号提醒。"
      : "本轮用满提醒已关闭。";
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "提醒设置失败";
  } finally {
    saving.value = false;
  }
}
async function refresh() {
  if (loading.value || saving.value) return;
  loading.value = true;
  try {
    const next = await api<TemporaryBurstData>("dashboard/temporary-burst");
    if (disposed) return;
    data.value = next;
    error.value = "";
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
      message: `所有启用的 Sub2API 账号参与本轮，已分配参与者的建议余额将设为 9999 美元，不会增加订阅容量。${data.value.auto_apply ? "自动应用已开启，将立即尝试写入余额。" : "需要手动应用余额建议。"}\n\n${data.value.enabled_account_count > 1 ? `多账号共享余额提醒：当前有 ${data.value.enabled_account_count} 个账号。参与者余额在有权使用且被路由到的账号间共享，无法只对某个账号放开。按账号结算不代表消费额度隔离。\n\n` : ""}首个账号换周期即全局退出；余额恢复取决于自动或手动应用。其他账号继续观测，分别换周期后按旧周期剩余小于 5% 的条件结算。\n\n开启前，请告知所有车友：爽蹬可能提前耗尽额度。若使用重置卡，下次重置将改为用卡后的 7 天。例如原定周一重置，周三用卡后下次变为下周三；前两天未使用的车友也会受影响。\n\n爽蹬本身不会修改重置时间。用卡前请与所有车友协商，权益结算不能弥补使用时间安排的变化。`,
      confirmLabel:
        data.value.enabled_account_count > 1
          ? "了解共享余额影响，开启爽蹬"
          : "确认开启临时爽蹬",
      acknowledgement:
        "我已告知所有车友：共享余额、爽蹬可能提前耗尽额度，使用重置卡会改变后续重置时间。",
      tone: "warning",
    }))
  )
    return;
  saving.value = true;
  error.value = notice.value = "";
  try {
    data.value = await api<TemporaryBurstData>("dashboard/temporary-burst", {
      method: "POST",
      body: jsonBody({ confirm: true, riders_notified: true }),
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
watch(
  () => data.value?.active,
  (active, previous) => {
    if (
      previous !== undefined &&
      active !== undefined &&
      active !== previous &&
      !saving.value
    ) {
      notice.value = "";
      emit("changed");
    }
  },
  { flush: "sync" },
);
onMounted(() => void refresh());
onBeforeUnmount(() => {
  disposed = true;
});
defineExpose({ refresh });
</script>

<template>
  <section
    id="temporary-burst"
    class="card col-span-12 bg-base-200 shadow-xs"
    :class="{ 'ring-1 ring-orange-500/60': data?.active }"
    aria-label="临时爽蹬"
  >
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
        >，按需使用，但不停止记账。各账号换周期后，只有上周期最终观测剩余小于 5%
        才结算借用权益；剩余大于或等于 5% 时不扣、不补，未用额度到期作废。
      </p>
      <p v-if="data?.active" class="text-sm">
        本轮预计结束：{{
          dateTime(data.expires_at)
        }}。任一账号提前换周期也会统一退出，其他账号按各自原周期结束时间结算。
      </p>
      <div
        v-if="data?.sampling.some((row) => row.accelerated)"
        class="flex flex-wrap gap-2 text-xs"
      >
        <span
          v-for="row in data.sampling"
          :key="row.account_id"
          class="badge badge-outline"
        >
          {{ row.account_name }} ·
          {{
            !data.monitoring_enabled
              ? "监控暂停"
              : `${row.accelerated ? "加速" : "常规"} ${row.interval_seconds / 60} 分钟一次`
          }}
        </span>
      </div>
      <p
        v-if="data && data.enabled_account_count > 1"
        class="text-sm text-warning"
      >
        全局共享余额 ·
        {{ data.enabled_account_count }}
        个启用账号。不能按账号隔离消费额度；请提前告知所有车友，并在使用重置卡前协商周期变化。
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
          class="btn btn-sm"
          :class="data?.reminder_enabled ? 'btn-warning' : 'btn-outline'"
          role="switch"
          aria-label="本轮用满提醒"
          :aria-checked="Boolean(data?.reminder_enabled)"
          :disabled="
            loading ||
            saving ||
            (!data?.reminder_enabled &&
              (!data?.reminder_email_ready ||
                !data?.session_id ||
                data.can_start))
          "
          @click="toggleReminder"
        >
          用满提醒：{{ data?.reminder_enabled ? "已开启" : "已关闭" }}
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
      <p class="text-xs leading-6 opacity-70">
        用满提醒仅对本轮生效：原周期账号最新观测达到 95%
        后，每半小时最多向管理员接收邮箱发送一次，换周期后停止。监控暂停时不会自动发送；失败详情见通知记录。本功能不会自动使用重置卡。
        <RouterLink
          v-if="data && !data.reminder_email_ready"
          to="/settings"
          class="link link-primary"
          >请先配置邮件服务和管理员接收邮箱，并发送测试邮件。</RouterLink
        >
        <span v-else-if="data?.can_start">开启爽蹬后可启用本轮提醒。</span>
      </p>
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
        <p v-if="cycle.settlement_context" class="mt-3 text-sm">
          {{ cycle.settlement_context.reason }}（剩余
          {{ percent(cycle.settlement_context.remaining_percent) }}）。
          额度观测：{{
            dateTime(cycle.settlement_context.quota_observed_at)
          }}，距原定重置
          {{ Math.round(cycle.settlement_context.seconds_before_reset / 60) }}
          分钟；不是重置瞬间的精确终值。
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
        <details v-if="cycle.carry_edits?.length" class="mt-3 text-xs">
          <summary class="cursor-pointer">管理员结转调整记录</summary>
          <ul class="mt-2 space-y-2">
            <li v-for="(edit, index) in cycle.carry_edits" :key="index">
              {{ dateTime(edit.edited_at) }} · {{ edit.admin_username }} ·
              {{
                cycle.members.find(
                  (member) => member.participant_id === edit.participant_id,
                )?.name ?? `参与者 ${edit.participant_id}`
              }}： {{ adjustment(edit.before) }} → {{ adjustment(edit.after) }}
            </li>
          </ul>
        </details>
      </details>
    </div>
    <ConfirmDialog ref="confirmation" />
  </section>
</template>
