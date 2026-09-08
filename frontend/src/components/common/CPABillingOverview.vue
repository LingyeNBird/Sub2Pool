<script setup lang="ts">
import { computed, ref } from "vue";
import type { CPAPoolSummary, CPAWeeklyDistribution } from "@/types/cpa";
import { api } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import { useDateTime } from "@/composables/useDateTime";
import { formatCurrency } from "@/utils/formatters";
import { cpaColor } from "./cpaColors";
import CPADistributionBar from "./CPADistributionBar.vue";
const props = defineProps<{ data: CPAPoolSummary }>();
const emit = defineEmits<{ refresh: [] }>();
const auth = useAuthStore();
const demo = import.meta.env.VITE_DEMO_MODE === "true";
const dateTime = useDateTime();
const billing = computed(() => props.data.billing_summary);
const billingTime = (value: string | null) =>
  value
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: billing.value?.timezone ?? "Asia/Shanghai",
        year: "numeric",
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).format(new Date(value))
    : "—";
const dialog = ref<HTMLDialogElement>();
const anchor = ref("");
const zone = ref("Asia/Shanghai");
const error = ref("");
const saving = ref(false);
const money = (n: number | null | undefined) =>
  n == null ? "未知" : formatCurrency(n);
const weeklyRemaining = (week: CPAWeeklyDistribution) =>
  week.remaining_usd ??
  (week.capacity_usd == null
    ? null
    : Math.max(0, week.capacity_usd - week.usage_usd));
const memberName = (id: number) =>
  props.data.members.find((m) => m.participant_id === id)?.participant_name ??
  "其他历史成员";
function openConfig() {
  anchor.value = billing.value?.anchor_date ?? "";
  zone.value = billing.value?.timezone ?? "Asia/Shanghai";
  error.value = "";
  dialog.value?.showModal();
}
async function save() {
  saving.value = true;
  error.value = "";
  try {
    await api(
      `cpa/billing-config?account_id=${props.data.selected_account_id}`,
      {
        method: "PUT",
        body: JSON.stringify({
          anchor_date: anchor.value || null,
          timezone: zone.value,
        }),
      },
    );
    dialog.value?.close();
    emit("refresh");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    saving.value = false;
  }
}
const monthlySegments = computed(() => [
  ...(billing.value?.members ?? []).map((m) => ({
    label: memberName(m.participant_id),
    value: m.usage_usd,
    color: cpaColor(m.participant_id),
  })),
  {
    label: "未归属",
    value: billing.value?.unattributed_usd ?? 0,
    color: "#64748b",
  },
  {
    label: "其他历史成员",
    value: billing.value?.other_members_usd ?? 0,
    color: "#a16207",
  },
]);
</script>
<template>
  <div class="space-y-4">
    <section
      v-for="week in data.weekly_distribution ?? []"
      :key="week.account_id"
      class="card bg-base-100 card-border"
    >
      <div class="card-body gap-4 p-5">
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 class="card-title">
              本周额度分布
              <span
                v-if="(data.weekly_distribution?.length ?? 0) > 1"
                class="text-sm font-normal"
                >{{ week.account_name }}</span
              >
            </h3>
            <p class="mt-1 text-xs text-base-content/60">
              {{ dateTime(week.started_at) }} —
              {{ dateTime(week.resets_at) }} 重置
            </p>
          </div>
          <div class="text-right">
            <div class="text-xs text-base-content/60">整车估算容量</div>
            <strong class="text-xl">{{ money(week.capacity_usd) }}</strong>
          </div>
        </div>
        <p
          v-if="week.capacity_estimate?.source === 'particle_filter'"
          class="text-sm text-base-content/70"
        >
          {{
            week.capacity_estimate.prior_only
              ? "粒子模型先验 · 尚待有效观测校准"
              : "粒子轨迹估计"
          }}
          <span
            v-if="
              week.capacity_estimate.lower_usd != null &&
              week.capacity_estimate.upper_usd != null
            "
          >
            · 90% 区间 {{ money(week.capacity_estimate.lower_usd) }} ～
            {{ money(week.capacity_estimate.upper_usd) }}</span
          >
          · {{ dateTime(week.capacity_estimate.as_of) }}
        </p>
        <p
          v-if="week.capacity_usd != null && week.coverage_complete === false"
          class="text-xs text-base-content/60"
        >
          估算剩余＝整车估算容量－已采集费用。漏采或未定价请求尚未扣除，实际上游剩余比例见下方。
        </p>
        <CPADistributionBar
          label="成员已采集消耗 / 估算剩余"
          percent-basis="本周额度"
          :capacity="week.capacity_usd"
          :segments="[
            ...week.members.map((m) => ({
              label: memberName(m.participant_id),
              value: m.usage_usd,
              color: cpaColor(m.participant_id),
            })),
            { label: '未归属', value: week.unattributed_usd, color: '#64748b' },
            {
              label: '其他历史成员',
              value: week.other_members_usd,
              color: '#a16207',
            },
            {
              label: '估算剩余',
              value: weeklyRemaining(week),
              color: '#94a3b8',
            },
          ]"
        />
        <p class="text-xs text-base-content/60">
          上游明确剩余
          {{
            week.upstream_remaining_percent == null
              ? "未知"
              : `${week.upstream_remaining_percent.toFixed(1)}%`
          }}
          · 额度估算 {{ dateTime(week.quota_as_of) }} · 最近请求
          {{ dateTime(week.requests_as_of)
          }}<span v-if="week.unpriced_request_count">
            · {{ week.unpriced_request_count }} 次请求未计价</span
          >
        </p>
      </div>
    </section>
    <section class="card bg-base-100 card-border">
      <div class="card-body gap-4 p-5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h3 class="card-title">订阅账期累计分布</h3>
          <button
            v-if="auth.isStaff"
            class="btn btn-ghost btn-sm"
            @click="openConfig"
          >
            {{ billing?.configured ? "账期设置" : "配置订阅账期" }}
          </button>
        </div>
        <p v-if="!billing?.configured" class="text-sm text-base-content/60">
          尚未配置订阅账期。{{
            auth.isStaff
              ? "请设置订阅起始日期与时区，系统将根据各周容量估算累计权益。"
              : "请联系管理员设置订阅起始日期与时区。"
          }}
        </p>
        <template v-else>
          <p v-if="demo" class="text-xs text-base-content/60">
            演示：四个完整周的合成场景，用于查看跨周协调效果；日期与金额不代表真实订阅。
          </p>
          <p class="text-xs text-base-content/60">
            {{ billingTime(billing.started_at) }} —
            {{ billingTime(billing.ended_at) }} · {{ billing.timezone }} ·
            美元均为估算
          </p>
          <div class="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <div>
              <p class="text-xs text-base-content/60">账期预计总容量</p>
              <strong class="text-2xl">{{
                money(billing.capacity_usd)
              }}</strong>
            </div>
            <div>
              <p class="text-xs text-base-content/60">累计已采集</p>
              <strong class="text-2xl">{{ money(billing.usage_usd) }}</strong>
            </div>
            <div>
              <p class="text-xs text-base-content/60">已失效额度</p>
              <strong class="text-2xl">{{ money(billing.expired_usd) }}</strong>
            </div>
            <div>
              <p class="text-xs text-base-content/60">当前及后续预计可用</p>
              <strong class="text-2xl">{{
                money(billing.available_usd)
              }}</strong>
            </div>
          </div>
          <CPADistributionBar
            label="容量来源 · 实色已有周期，浅色未来预测"
            percent-basis="账期总额度"
            :capacity="billing.capacity_usd"
            :segments="[
              {
                label: '已知周期容量',
                value: billing.actual_capacity_usd,
                color: '#3b82f6',
              },
              {
                label: '未来预测容量',
                value: billing.future_capacity_usd,
                color: '#3b82f6',
                forecast: true,
              },
            ]"
          />
          <CPADistributionBar
            label="累计消耗 · 百分比占整车账期预计总容量"
            percent-basis="账期总额度"
            :capacity="billing.capacity_usd"
            :segments="monthlySegments"
          />
          <p class="text-sm">
            未分配权益
            {{
              money(billing.unallocated_usd)
            }}。个人预计剩余权益用于跨周协调；已失效额度无法在下周恢复，建议不会修改份额或限制调用。
          </p>
          <p v-if="billing.reasons.length" class="alert text-sm" role="status">
            结算待补全：{{
              billing.reasons.join("；")
            }}。保留可用容量估计和已采集金额，缺口不计作剩余。
          </p>
          <details class="collapse-arrow collapse border border-base-300">
            <summary class="collapse-title text-sm font-medium">
              各周期容量与更新时间
            </summary>
            <div class="collapse-content space-y-2">
              <div
                v-for="(cycle, i) in billing.cycles"
                :key="i"
                class="flex flex-wrap justify-between gap-2 text-xs"
              >
                <span
                  >{{ cycle.account_name }} ·
                  {{ billingTime(cycle.started_at) }} —
                  {{ billingTime(cycle.ended_at) }}</span
                ><span
                  >{{
                    {
                      historical: "历史估算",
                      current: "当前估算",
                      future: "未来预测",
                    }[cycle.kind]
                  }}
                  {{ money(cycle.capacity_usd) }} ·
                  {{
                    cycle.quota_as_of
                      ? dateTime(cycle.quota_as_of)
                      : "预测 / 无可靠观测"
                  }}</span
                >
                <p v-if="cycle.reasons.length" class="w-full text-warning">
                  {{ cycle.reasons.join("；") }}
                </p>
              </div>
            </div>
          </details>
        </template>
      </div>
    </section>
    <dialog ref="dialog" class="modal">
      <div class="modal-box">
        <h3 class="text-lg font-bold">订阅账期设置</h3>
        <form class="mt-4 space-y-4" @submit.prevent="save">
          <label class="fieldset"
            ><span class="fieldset-legend">订阅起始日期</span
            ><input v-model="anchor" type="date" class="input w-full" /><span
              class="label whitespace-normal"
              >按此日期每月续期，短月取月末，下月恢复原日期。清空可取消配置。</span
            ></label
          ><label class="fieldset"
            ><span class="fieldset-legend">账期时区</span
            ><input
              v-model="zone"
              class="input w-full"
              required
              placeholder="Asia/Shanghai"
          /></label>
          <p v-if="error" class="text-error" role="alert">{{ error }}</p>
          <div class="modal-action">
            <button type="button" class="btn" @click="dialog?.close()">
              取消</button
            ><button type="submit" class="btn btn-primary" :disabled="saving">
              {{ saving ? "保存中…" : "保存并重算" }}
            </button>
          </div>
        </form>
      </div>
      <form method="dialog" class="modal-backdrop"><button>关闭</button></form>
    </dialog>
  </div>
</template>
