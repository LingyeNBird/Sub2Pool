<script setup lang="ts">
import { formatCurrency } from "@/utils/formatters";
import { computed, ref } from "vue";
import type { AccountStatusAccount, CPAResetPreview } from "@/types/accounts";
import { useDateTime } from "@/composables/useDateTime";
import { useAuthStore } from "@/stores/auth";
import { api, jsonBody } from "@/services/api";
import CPAQuotaMetricList from "./CPAQuotaMetricList.vue";
const props = defineProps<{
  account: AccountStatusAccount;
  loading: boolean;
}>();
const emit = defineEmits<{ refresh: [] }>();
const auth = useAuthStore();
const time = useDateTime();
const detail = computed(() => props.account.cpa_quota);
const dialog = ref<HTMLDialogElement | null>(null);
const preview = ref<CPAResetPreview | null>(null);
const busy = ref(false);
const message = ref("");
const reset = computed(() => detail.value?.reset);
const pending = computed(() =>
  reset.value?.history.some((item) => item.status === "unknown"),
);
const canStart = computed(
  () =>
    reset.value?.can_reset &&
    !busy.value &&
    !props.loading &&
    ((reset.value.available_count ?? 0) > 0 || pending.value),
);
const remaining = (used: number | null) =>
  used == null ? null : Math.max(0, 100 - used);
const percent = (used: number | null) =>
  used == null ? "未知" : `${Number(used.toFixed(2))}%`;
const status = computed(() =>
  !props.account.enabled
    ? "监控已停用"
    : props.account.runtime?.status === "active"
      ? "正常"
      : props.account.runtime?.status === "disabled"
        ? "已禁用"
        : props.account.runtime?.status === "error"
          ? "异常"
          : "状态未知",
);
async function openReset() {
  busy.value = true;
  message.value = "";
  preview.value = null;
  dialog.value?.showModal();
  try {
    preview.value = await api<CPAResetPreview>(
      `account-status/cpa/${props.account.id}/reset-preview`,
      { method: "POST" },
    );
  } catch (error) {
    message.value = error instanceof Error ? error.message : "预览重置失败";
  } finally {
    busy.value = false;
  }
}
async function confirmReset() {
  if (!preview.value || busy.value) return;
  busy.value = true;
  message.value = "";
  try {
    await api(
      `account-status/cpa/${props.account.id}/resets/${preview.value.id}/confirm`,
      { method: "POST", body: jsonBody({ confirmed: true }) },
    );
    dialog.value?.close();
    preview.value = null;
    message.value = "额度重置成功，正在刷新额度。";
    emit("refresh");
  } catch (error) {
    message.value =
      error instanceof Error ? error.message : "重置失败，请刷新额度核对";
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <article
    class="card col-span-12 min-w-0 border border-base-300 bg-base-100"
    :data-testid="`cpa-account-status-${account.id}`"
  >
    <div class="card-body gap-7 p-4 sm:p-7">
      <header
        class="flex flex-wrap items-center justify-between gap-4 border-b border-base-300 pb-5"
      >
        <div class="flex min-w-0 items-center gap-3">
          <span class="rounded-box bg-secondary/10 p-3 text-secondary"
            ><AppIcon name="code-bracket" class="size-6"
          /></span>
          <div class="min-w-0">
            <h2 class="text-xl font-semibold break-all">
              {{ account.runtime?.name || account.name }}
            </h2>
            <p class="mt-1 text-sm text-base-content/60">
              Codex · {{ account.runtime?.account_type || "套餐未知" }} · CPA
            </p>
          </div>
        </div>
        <span
          class="badge badge-outline"
          :class="status === '正常' ? 'badge-success' : 'badge-warning'"
          >{{ status }}</span
        >
      </header>
      <section aria-label="凭证总体用量" class="space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h3 class="text-lg font-semibold">凭证总体用量</h3>
          <p v-if="detail" class="text-xs text-base-content/60 sm:text-right">
            统计范围 {{ time(detail.totals.started_at) }} —
            {{ time(detail.totals.ended_at) }}
          </p>
        </div>
        <CPAQuotaMetricList :metrics="detail?.totals.metrics" tiles />
        <p
          v-if="detail?.totals.metrics.unpriced_request_count"
          class="text-xs text-base-content/70"
        >
          {{ detail.totals.metrics.unpriced_request_count }}
          次请求缺少价格，未计入估算费用。
        </p>
      </section>
      <section class="space-y-4" aria-label="额度窗口">
        <div class="flex flex-wrap items-center gap-3">
          <h3 class="text-lg font-semibold">额度与重置</h3>
          <p class="text-sm text-base-content/60">
            账号级配额 · 按窗口统计已采集用量
          </p>
        </div>
        <div v-if="!detail?.windows.length" role="status" class="alert">
          暂无可用额度窗口，请刷新或检查 CPA 连接。
        </div>
        <section
          v-for="window in detail?.windows ?? []"
          :key="window.id"
          class="card border border-base-300 bg-base-100"
        >
          <div class="card-body gap-5 p-4 sm:p-5">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div class="flex flex-wrap items-center gap-2">
                <span class="rounded-box bg-info/10 p-2 text-info"
                  ><AppIcon name="calendar-days" class="size-5"
                /></span>
                <h4 class="text-lg font-semibold">{{ window.label }}</h4>
                <span class="badge badge-ghost">{{
                  window.reset_at
                    ? `重置于 ${time(window.reset_at)}`
                    : "重置时间未知"
                }}</span>
              </div>
              <p class="shrink-0 text-right text-base-content">
                <span class="mr-2 text-xs">剩余</span
                ><strong class="text-3xl tabular-nums">{{
                  percent(remaining(window.used_percent))
                }}</strong>
              </p>
            </div>
            <progress
              v-if="window.used_percent != null"
              class="progress h-3 w-full"
              :class="
                window.used_percent >= 95
                  ? 'progress-error'
                  : window.used_percent >= 80
                    ? 'progress-warning'
                    : 'progress-success'
              "
              :value="remaining(window.used_percent) ?? 0"
              max="100"
              :aria-label="`${window.label}剩余 ${percent(remaining(window.used_percent))}`"
            ></progress>
            <div v-if="window.current" class="grid gap-4 lg:grid-cols-3">
              <section class="card border border-base-300">
                <div class="card-body gap-5 p-4">
                  <div>
                    <h5 class="font-semibold">上个窗口用量</h5>
                    <p class="mt-1 text-xs text-base-content/60">
                      {{
                        window.previous
                          ? `${time(window.previous.started_at)} — ${time(window.previous.ended_at)}`
                          : "暂无可靠的上个窗口记录"
                      }}
                    </p>
                  </div>
                  <CPAQuotaMetricList :metrics="window.previous?.metrics" />
                  <p
                    v-if="
                      window.previous?.notice ||
                      (window.previous && !window.previous.coverage_complete)
                    "
                    class="text-xs text-base-content/70"
                  >
                    {{
                      window.previous.notice || "采集不完整，仅展示已采集用量。"
                    }}
                  </p>
                </div>
              </section>
              <section class="card border border-base-300">
                <div class="card-body gap-5 p-4">
                  <div>
                    <h5 class="font-semibold">当前窗口已用</h5>
                    <p class="mt-1 text-xs text-base-content/60">
                      {{ time(window.current.started_at) }} —
                      {{ time(window.current.ended_at) }}
                    </p>
                  </div>
                  <CPAQuotaMetricList :metrics="window.current.metrics" />
                </div>
              </section>
              <section class="card border border-info/20 bg-info/5">
                <div class="card-body gap-5 p-4">
                  <div>
                    <h5 class="flex items-center gap-2 font-semibold">
                      <AppIcon
                        name="arrow-trending-up"
                        class="size-5 text-info"
                      />当前窗口预测
                    </h5>
                    <p class="mt-1 text-xs text-base-content/60">
                      {{
                        window.capacity_estimate
                          ? window.capacity_estimate.source ===
                            "particle_filter"
                            ? "复用粒子轨迹的容量估计"
                            : "平均恒定模型容量估计"
                          : "按已用比例估算满额总量"
                      }}
                    </p>
                  </div>
                  <div v-if="window.capacity_estimate" class="space-y-2">
                    <p class="text-xs text-base-content/60">周期估算容量</p>
                    <strong class="text-2xl">{{
                      formatCurrency(window.capacity_estimate.capacity_usd)
                    }}</strong>
                    <p
                      v-if="
                        window.capacity_estimate.lower_usd != null &&
                        window.capacity_estimate.upper_usd != null
                      "
                      class="text-xs text-base-content/60"
                    >
                      90% 区间
                      {{ formatCurrency(window.capacity_estimate.lower_usd) }}
                      ～
                      {{ formatCurrency(window.capacity_estimate.upper_usd) }}
                    </p>
                    <p class="text-xs text-base-content/60">
                      估算更新 {{ time(window.capacity_estimate.as_of) }}
                    </p>
                    <p
                      v-if="window.capacity_estimate.prior_only"
                      class="text-xs text-warning"
                    >
                      模型先验，尚待有效观测校准。
                    </p>
                  </div>
                  <CPAQuotaMetricList
                    v-else
                    :metrics="window.prediction"
                    prediction
                  />
                  <p
                    class="border-t border-dashed border-info/30 pt-3 text-xs text-base-content/60"
                  >
                    {{
                      window.capacity_estimate
                        ? "模型估计，不代表保证可用的余额；不补造漏采消耗。"
                        : window.prediction
                          ? "线性估算，不代表保证可用的余额；不预测成功率。"
                          : "数据不足，暂不预测。"
                    }}
                  </p>
                </div>
              </section>
            </div>
            <p v-if="window.notice" class="text-sm text-base-content/70">
              {{ window.notice }}
            </p>
            <footer
              class="flex flex-wrap gap-x-5 gap-y-2 text-xs text-base-content/60"
            >
              <span class="flex items-center gap-1"
                ><AppIcon name="circle-stack" class="size-4" />{{
                  window.source
                }}</span
              ><span class="flex items-center gap-1"
                ><AppIcon name="clock" class="size-4" />{{
                  time(window.updated_at)
                }}
                最近观测</span
              ><span class="flex items-center gap-1"
                ><AppIcon name="shield-check" class="size-4" />{{
                  window.boundary === "provider"
                    ? "边界：上游明确时间"
                    : "边界：无法可靠确定"
                }}</span
              >
            </footer>
          </div>
        </section>
      </section>
      <section class="card border border-base-300">
        <div class="card-body gap-4 p-4 sm:p-5">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex items-center gap-3">
              <span class="rounded-box bg-info/10 p-2 text-info"
                ><AppIcon name="arrow-path" class="size-6"
              /></span>
              <div>
                <h3 class="text-lg font-semibold">重置记录</h3>
                <p class="text-xs text-base-content/60">
                  可用的 Codex 重置额度
                </p>
              </div>
            </div>
            <div class="flex items-center gap-4">
              <p class="text-sm text-base-content/60">
                剩余
                <strong class="text-2xl text-base-content">{{
                  reset?.available_count ?? "未知"
                }}</strong>
                次
              </p>
              <button
                v-if="reset?.can_reset"
                class="btn"
                :disabled="!canStart"
                @click="openReset"
              >
                {{ pending ? "核对重置结果" : "重置额度" }}</button
              ><span v-else class="badge badge-ghost"
                >仅车主 / 管理员可重置</span
              >
            </div>
          </div>
          <p
            v-if="!reset?.history.length"
            class="rounded-box bg-base-200 p-3 text-sm text-base-content/60"
          >
            {{
              reset?.available_count === 0
                ? "暂无可用重置额度。"
                : reset?.available_count == null
                  ? "上游尚未提供可用重置次数。"
                  : "暂无本系统发起的重置记录。"
            }}
          </p>
          <ul v-else class="space-y-2 text-sm">
            <li
              v-for="item in reset.history"
              :key="item.id"
              class="flex flex-wrap justify-between gap-2"
            >
              <span>{{ time(item.finished_at || item.created_at) }}</span
              ><span>{{
                item.status === "succeeded"
                  ? "已重置"
                  : item.status === "failed"
                    ? "重置被拒绝"
                    : "结果待核对"
              }}</span>
            </li>
          </ul>
        </div>
      </section>
      <div
        v-if="account.warnings.length || account.runtime?.error_message"
        class="alert text-sm alert-warning"
      >
        <span>{{
          [...account.warnings, account.runtime?.error_message]
            .filter(Boolean)
            .join("；")
        }}</span>
      </div>
      <footer
        class="flex flex-wrap items-center justify-between gap-3 border-t border-base-300 pt-4"
      >
        <p class="text-xs text-base-content/60">
          金额为估算值，统计仅包含本系统已采集请求。
        </p>
        <div class="flex gap-2">
          <RouterLink
            v-if="auth.canAccess('statistics')"
            class="btn btn-sm"
            :to="{ path: '/cpa-requests', query: { account_id: account.id } }"
            >请求明细</RouterLink
          ><button
            class="btn btn-sm"
            :disabled="loading || busy"
            @click="emit('refresh')"
          >
            <AppIcon name="arrow-path" class="size-4" />刷新额度
          </button>
        </div>
      </footer>
    </div>
  </article>
  <dialog
    ref="dialog"
    class="modal"
    aria-label="确认重置 CPA 额度"
    @cancel="busy && $event.preventDefault()"
  >
    <div class="modal-box">
      <h3 class="text-lg font-semibold">确认重置额度？</h3>
      <p class="mt-3 text-sm">
        账号：{{ account.runtime?.name || account.name }}
      </p>
      <p v-if="preview" class="mt-3 text-sm">
        {{
          preview.status === "unknown"
            ? "上次请求结果未确认。再次确认会复用原请求标识核对重置结果。"
            : `将消耗 1 次 Codex 重置额度，当前可用 ${preview.available_count} 次。操作成功后不能撤销。`
        }}
      </p>
      <p v-if="busy" role="status" class="mt-3 text-sm">正在处理…</p>
      <p v-if="message" role="alert" class="mt-4 alert">{{ message }}</p>
      <div class="modal-action">
        <button class="btn" :disabled="busy" @click="dialog?.close()">
          取消</button
        ><button
          v-if="preview"
          class="btn btn-warning"
          :disabled="busy"
          @click="confirmReset"
        >
          {{
            preview.status === "unknown" ? "确认核对原请求" : "确认消耗并重置"
          }}
        </button>
      </div>
    </div>
  </dialog>
</template>
