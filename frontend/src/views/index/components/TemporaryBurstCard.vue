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
const carryoverEnabled = ref(true);
watch(
  () => data.value?.carryover_enabled,
  (value) => {
    if (typeof value === "boolean" && !data.value?.can_start)
      carryoverEnabled.value = value;
  },
  { immediate: true },
);
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
  const carryover = carryoverEnabled.value;
  if (
    !(await confirmation.value?.open({
      title: "开启临时爽蹬？",
      message: `所有启用的 Sub2API 账号参与本轮，余额建议统一为 9999，不增加套餐容量。${data.value.auto_apply ? "系统会尝试自动应用余额。" : "开启后请手动应用余额建议。"}\n\n${carryover ? "结转模式：按借用权益在后续周期补偿、扣除，不看账号是否用满。无需逐一通知；未被借用的闲置额度不会保留。" : "不结转模式：本轮多用不追账、少用不补偿，可能提前耗尽其他车友计划使用的额度。请先告知所有车友。"}\n\n${data.value.enabled_account_count > 1 ? `当前有 ${data.value.enabled_account_count} 个账号，共享钱包不能按账号隔离消费。\n\n` : ""}首个账号换周期后退出爽蹬，各账号按选定模式结束本轮。本轮模式不能中途切换；提前使用重置卡仍建议沟通使用安排。`,
      confirmLabel:
        data.value.enabled_account_count > 1
          ? "了解共享余额影响，开启爽蹬"
          : "确认开启临时爽蹬",
      acknowledgement: carryover
        ? undefined
        : "我已告知所有车友：本轮不结转，多用不追账、少用不补偿，可能提前耗尽额度。",
      tone: "warning",
    }))
  )
    return;
  saving.value = true;
  error.value = notice.value = "";
  try {
    data.value = await api<TemporaryBurstData>("dashboard/temporary-burst", {
      method: "POST",
      body: jsonBody({
        confirm: true,
        carryover_enabled: carryover,
        riders_notified: !carryover,
      }),
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

async function stop() {
  if (saving.value || !data.value?.can_stop) return;
  const sessionId = data.value.session_id;
  if (
    !(await confirmation.value?.open({
      title: "你确定提前终止爽蹬吗？",
      message: `立即恢复普通额度建议，停止本轮加速采样和提醒，并取消本轮全部后续结转。已经超用的部分不追账，也不补偿少用者。此操作不可撤销，本周期不能再次开启。\n\n${data.value.auto_apply ? "系统将尝试自动应用普通余额建议；失败项需手动重试。" : "结束后请手动应用普通余额建议，收回上游的高余额。仅结束模式不会直接改动上游余额。"}`,
      confirmLabel: "确认提前终止",
      tone: "error",
    }))
  )
    return;
  saving.value = true;
  error.value = notice.value = "";
  try {
    data.value = await api<TemporaryBurstData>("dashboard/temporary-burst", {
      method: "DELETE",
      body: jsonBody({ confirm: true, session_id: sessionId }),
    });
    const result = data.value.application;
    notice.value = data.value.auto_apply
      ? `已提前终止，不再产生本轮结转。普通建议已应用 ${result?.applied ?? 0} 人，失败 ${result?.failed ?? 0} 人。`
      : "已提前终止，不再产生本轮结转。请手动应用普通余额建议。";
    emit("changed");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "提前终止失败";
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
        >，按需使用，照常记账。结转模式下借用的权益会在后续周期补偿或扣除；不结转模式下多用不追账、少用不补偿。
      </p>
      <fieldset
        class="fieldset"
        :disabled="saving || loading || !data?.can_start"
      >
        <legend class="fieldset-legend">爽蹬结算方式</legend>
        <div class="flex flex-wrap gap-5">
          <label class="label cursor-pointer"
            ><input
              v-model="carryoverEnabled"
              type="radio"
              name="burst-mode"
              class="radio radio-sm"
              :value="true"
            />结转：借用的权益以后还</label
          >
          <label class="label cursor-pointer"
            ><input
              v-model="carryoverEnabled"
              type="radio"
              name="burst-mode"
              class="radio radio-sm"
              :value="false"
            />不结转：本轮多用不追账</label
          >
        </div>
      </fieldset>
      <p v-if="data?.session_id" class="text-sm font-medium">
        本轮模式：{{ data.carryover_enabled ? "结转" : "不结转"
        }}{{ data.terminated_at ? " · 已提前终止" : "" }}
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
        个启用账号。不能按账号隔离消费额度；不结转模式需提前告知所有车友，使用重置卡仍建议沟通。
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
          v-if="data?.can_stop"
          type="button"
          class="btn btn-error btn-sm"
          :disabled="saving || loading"
          @click="stop"
        >
          提前终止爽蹬
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
                !data.can_stop))
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
        {{
          data.terminated_at
            ? "本轮已提前终止，本周期不能再次开启。"
            : "仍有账号等待原周期结束；完成后才能开始新一轮。"
        }}
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
          {{ cycle.settlement_context.reason }}
          <template v-if="cycle.settlement_context.quota_observed_at">
            （剩余 {{ percent(cycle.settlement_context.remaining_percent) }}）。
            额度观测：{{
              dateTime(cycle.settlement_context.quota_observed_at)
            }}，距原定重置
            {{
              Math.round(
                (cycle.settlement_context.seconds_before_reset ?? 0) / 60,
              )
            }}
            分钟；不是重置瞬间的精确终值。
          </template>
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
