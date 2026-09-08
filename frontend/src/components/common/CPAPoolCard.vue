<script setup lang="ts">
import { computed } from "vue";
import { useAuthStore } from "@/stores/auth";
import CPAOwnerClaim from "./CPAOwnerClaim.vue";
import CPAMemberQuotaCard from "./CPAMemberQuotaCard.vue";
import type { CPAPoolSummary } from "@/types/cpa";
import { formatCurrency } from "@/utils/formatters";
import { useDateTime } from "@/composables/useDateTime";
const formatDateTime = useDateTime();
const formatNumber = (value: number) => value.toLocaleString();

const props = defineProps<{ data: CPAPoolSummary }>();
const emit = defineEmits<{ refresh: [] }>();
const auth = useAuthStore();
const members = computed(() =>
  [...props.data.members].sort((a, b) => Number(b.is_self) - Number(a.is_self)),
);
</script>

<template>
  <section
    class="card col-span-12 min-w-0 bg-base-200 shadow-xs"
    data-testid="cpa-pool-summary"
  >
    <div class="card-body gap-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="card-title">{{ data.pool_name }} · CPA 拼车</h2>
        <span class="badge badge-outline">额度展示 · 尚未启用自动限制</span>
      </div>
      <p class="text-sm opacity-60">
        按各账号当前周期汇总，美元金额为本地模型价格估算。成员可看同车汇总，逐次请求仅本人和管理员可见。
      </p>
      <p class="text-xs opacity-60">
        请求统计更新于
        {{
          formatDateTime(data.generated_at)
        }}。已用和剩余权益截至各账号的额度更新时间，之后的新请求尚未参与额度估算。
      </p>
      <p v-if="data.partial_scope" class="text-sm">
        当前仅汇总你获授权的池内账号。
      </p>
      <div
        v-if="data.collector.pending_count || !data.collector.connected"
        role="status"
        class="alert"
      >
        {{
          data.collector.connected
            ? `有 ${data.collector.pending_count} 条事件待写入，用量可能延迟`
            : "采集器未连接，最新请求数据可能尚未收齐"
        }}
      </div>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h3 class="text-lg font-semibold">
          成员额度 · {{ data.members.length }} 人
        </h3>
        <div class="flex flex-wrap gap-2">
          <RouterLink
            v-if="auth.canAccess('statistics')"
            :to="{
              path: '/cpa-requests',
              query: { account_id: data.selected_account_id },
            }"
            class="btn btn-sm"
            >{{ auth.isStaff ? "查看请求明细" : "我的请求明细" }}</RouterLink
          >
          <RouterLink
            v-if="auth.isStaff"
            to="/allocation?provider=cpa"
            class="btn btn-sm"
            >分配 CPA 份额</RouterLink
          >
        </div>
      </div>
      <div
        v-if="members.length"
        class="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
      >
        <CPAMemberQuotaCard
          v-for="member in members"
          :key="member.participant_id"
          :member="member"
          :accounts="data.accounts"
          :selected-account-id="data.selected_account_id"
        />
      </div>
      <div v-else class="alert" role="status">
        <span
          >尚未分配 CPA 拼车成员。请先绑定参与者的 Key，再为 CPA
          池分配份额。</span
        ><RouterLink
          v-if="auth.isStaff"
          to="/participants?provider=cpa"
          class="btn btn-sm"
          >绑定 CPA Key</RouterLink
        >
      </div>
      <p
        v-if="members.some((member) => !member.quota_available)"
        class="text-xs text-base-content/60"
      >
        已采集消耗正常展示；剩余额度待估算，原因见下方账号状态。
      </p>
      <div
        v-if="data.unattributed.request_count"
        class="rounded-box border border-base-300 bg-base-100 p-4"
      >
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 class="font-medium">未归属 / 未分配成员的消耗</h3>
            <p class="mt-2 text-sm">
              {{ formatCurrency(data.unattributed.usage_usd) }}（估算） ·
              {{ data.unattributed.request_count.toLocaleString() }} 次请求
            </p>
          </div>
          <RouterLink
            v-if="auth.isStaff"
            to="/participants?provider=cpa"
            class="btn btn-sm"
            >检查 Key 归属</RouterLink
          >
        </div>
        <p class="mt-2 text-xs leading-5 text-base-content/60">
          单独保留，不分摊给其他成员。绑定 Key
          默认从绑定时刻生效；已有请求需由管理员预览并认领。管理员可在下方账号状态中认领历史未归属请求；认领不会补回断线期间未采集的数据。
        </p>
      </div>
      <div
        class="grid gap-3"
        :class="data.accounts.length > 1 ? 'lg:grid-cols-2' : ''"
      >
        <div
          v-for="account in data.accounts"
          :key="account.account_id"
          class="card bg-base-100"
        >
          <div class="card-body gap-1 p-4">
            <h3 class="font-semibold">
              {{
                data.accounts.length > 1
                  ? account.account_name
                  : "账号状态与归属"
              }}
            </h3>
            <p v-if="account.owner.status === 'active'" class="text-sm">
              车主：{{ account.owner.participant_name }}。{{
                formatDateTime(account.owner.started_at)
              }}
              起，未匹配成员 Key 的请求计入车主。
            </p>
            <p v-else class="text-sm">
              {{
                account.owner.status === "ambiguous"
                  ? "此池有多位车主，请保留唯一车主身份后启用自动归属。"
                  : "此池尚无车主，在参与者管理中设为车主并加入该 CPA 池后，未匹配 Key 的新请求将计入车主。"
              }}
            </p>
            <CPAOwnerClaim
              v-if="auth.isStaff"
              :account="account"
              @refresh="emit('refresh')"
            />
            <p class="text-xs opacity-60">
              额度更新 {{ formatDateTime(account.quota_as_of) }} · 最近请求
              {{ formatDateTime(account.requests_as_of) }}
            </p>
            <p class="text-xs opacity-60">
              周期重置 {{ formatDateTime(account.resets_at) }}
            </p>
            <p v-if="!account.quota_available" class="text-sm">
              {{
                account.quota_unavailable_reasons?.join("；") ||
                "额度数据不足：等待有效观测、完整采集区间及模型价格。"
              }}
            </p>
            <p v-if="account.coverage.uncertain_end" class="text-xs opacity-60">
              存在未确认的断线截止点。
            </p>
            <p v-if="!account.coverage.complete" class="text-xs opacity-60">
              历史认领和补价无法恢复漏采请求；后续完整采集周期具备有效观测和份额依据后，才能估算剩余。
            </p>
          </div>
        </div>
      </div>
      <details class="collapse-arrow collapse border border-base-300">
        <summary class="collapse-title font-medium">成员用量汇总表</summary>
        <div class="collapse-content">
          <div class="overflow-x-auto">
            <table class="table">
              <thead>
                <tr>
                  <th>成员</th>
                  <th>份额</th>
                  <th>请求 / Token</th>
                  <th>请求估算费用</th>
                  <th>权益总额</th>
                  <th>已用权益</th>
                  <th>剩余权益</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="member in data.members" :key="member.participant_id">
                  <td>
                    <span class="font-medium">{{
                      member.participant_name
                    }}</span>
                    <span v-if="member.is_self" class="badge badge-sm"
                      >本人可见明细</span
                    >
                    <span
                      v-if="member.is_overused"
                      class="badge badge-sm badge-warning"
                      >已超额</span
                    >
                  </td>
                  <td>
                    {{
                      member.share_percent == null
                        ? "未分配"
                        : `${member.share_percent}%`
                    }}
                  </td>
                  <td>
                    {{ formatNumber(member.request_count) }} /
                    {{ formatNumber(member.token_count) }}
                  </td>
                  <td>
                    {{ formatCurrency(member.usage_usd) }}
                    <p v-if="member.unpriced_request_count" class="text-xs">
                      {{ member.unpriced_request_count }} 条未计价
                    </p>
                  </td>
                  <td>
                    {{
                      member.quota_available
                        ? formatCurrency(member.expected_entitlement_usd)
                        : "数据不足"
                    }}
                  </td>
                  <td>
                    {{
                      member.quota_available
                        ? formatCurrency(member.consumed_entitlement_usd)
                        : "数据不足"
                    }}
                  </td>
                  <td>
                    {{
                      member.quota_available
                        ? formatCurrency(member.remaining_entitlement_usd)
                        : "数据不足"
                    }}
                  </td>
                </tr>
                <tr v-if="!data.members.length">
                  <td colspan="7">
                    尚未分配 CPA 拼车成员，请在参与者管理中绑定 Key，并配置 CPA
                    额度池。
                  </td>
                </tr>
                <tr v-if="data.unattributed.request_count">
                  <td>未归属 / 未分配成员</td>
                  <td>—</td>
                  <td>
                    {{ formatNumber(data.unattributed.request_count) }} /
                    {{ formatNumber(data.unattributed.token_count) }}
                  </td>
                  <td>
                    {{ formatCurrency(data.unattributed.usage_usd) }}
                    <p
                      v-if="data.unattributed.unpriced_request_count"
                      class="text-xs"
                    >
                      {{ data.unattributed.unpriced_request_count }} 条未计价
                    </p>
                  </td>
                  <td colspan="3">单独保留，不分摊给成员</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </details>
    </div>
  </section>
</template>
