<script setup lang="ts">
import type { CPAPoolSummary } from "@/types/cpa";
import { formatCurrency } from "@/utils/formatters";
import { useDateTime } from "@/composables/useDateTime";
const formatDateTime = useDateTime();
const formatNumber = (value: number) => value.toLocaleString();

defineProps<{ data: CPAPoolSummary }>();
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
      <div class="grid gap-3 md:grid-cols-2">
        <div
          v-for="account in data.accounts"
          :key="account.account_id"
          class="card bg-base-100"
        >
          <div class="card-body gap-1 p-4">
            <h3 class="font-semibold">{{ account.account_name }}</h3>
            <p class="text-xs opacity-60">
              额度更新 {{ formatDateTime(account.quota_as_of) }} · 最近请求
              {{ formatDateTime(account.requests_as_of) }}
            </p>
            <p class="text-xs opacity-60">
              周期重置 {{ formatDateTime(account.resets_at) }}
            </p>
            <p v-if="!account.quota_available" class="text-sm">
              额度数据不足：等待有效观测、完整采集区间及模型价格。
            </p>
            <p v-if="!account.coverage.complete" class="text-sm">
              采集缺口 {{ account.coverage.gaps.length }} 段{{
                account.coverage.uncertain_end ? "，存在未确认的断线截止点" : ""
              }}
            </p>
          </div>
        </div>
      </div>
      <div
        v-if="data.members.some((member) => member.is_self)"
        class="grid gap-3 lg:grid-cols-2"
      >
        <div
          v-for="member in data.members.filter((member) => member.is_self)"
          :key="member.participant_id"
          class="card bg-base-100"
        >
          <div class="card-body">
            <h3 class="card-title">{{ member.participant_name }}的额度</h3>
            <p>
              合同份额
              {{
                member.share_percent == null
                  ? "未分配"
                  : `${member.share_percent}%`
              }}
            </p>
            <div class="grid grid-cols-3 gap-3">
              <div>
                <p class="text-xs opacity-60">权益总额</p>
                <p class="font-semibold">
                  {{
                    member.quota_available
                      ? formatCurrency(member.expected_entitlement_usd)
                      : "数据不足"
                  }}
                </p>
              </div>
              <div>
                <p class="text-xs opacity-60">已用权益</p>
                <p class="font-semibold">
                  {{
                    member.quota_available
                      ? formatCurrency(member.consumed_entitlement_usd)
                      : "数据不足"
                  }}
                </p>
              </div>
              <div>
                <p class="text-xs opacity-60">剩余权益</p>
                <p class="font-semibold">
                  {{
                    member.quota_available
                      ? formatCurrency(member.remaining_entitlement_usd)
                      : "数据不足"
                  }}
                </p>
              </div>
            </div>
            <p v-if="member.is_overused" class="text-sm">
              已超出分配额度，当前仍可调用。
            </p>
          </div>
        </div>
      </div>
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
                <span class="font-medium">{{ member.participant_name }}</span>
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
  </section>
</template>
