<script setup lang="ts">
import type { CorrectionBreakdown } from "@/types/common";
import { correctionTotal, formatCorrectionCurrency } from "@/utils/formatters";

defineProps<{ breakdown: CorrectionBreakdown }>();
const items = [
  { key: "fast_correction_usd", label: "FAST 修正" },
  { key: "long_context_correction_usd", label: "长上下文修正" },
  { key: "model_correction_usd", label: "模型倍率修正" },
] as const;
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-baseline justify-between gap-2">
      <span class="font-semibold">修正合计</span>
      <strong class="text-xl tabular-nums">{{
        formatCorrectionCurrency(correctionTotal(breakdown))
      }}</strong>
    </div>
    <dl class="rounded-box bg-base-200 p-4">
      <div
        v-for="item in items"
        :key="item.key"
        class="flex items-baseline justify-between gap-4 py-2"
      >
        <dt>{{ item.label }}</dt>
        <dd class="font-mono tabular-nums">
          {{ formatCorrectionCurrency(breakdown[item.key]) }}
        </dd>
      </div>
    </dl>
    <p class="text-sm opacity-65">
      按 FAST → 长上下文 →
      模型倍率依次计算，各项增减额相加即为合计。负数表示减少成本。
    </p>
    <div
      v-if="
        breakdown.correction_facts_complete === false ||
        breakdown.legacy_fast_only
      "
      class="alert text-sm alert-warning"
    >
      <span
        >包含缺少原始请求事实的旧区间。仅保留其已记录的 FAST
        修正，不猜测长上下文或模型倍率修正；已有完整事实的区间按当前规则重算。</span
      >
    </div>
    <div
      v-if="breakdown.unknown_long_context_request_count"
      class="alert text-sm alert-warning"
    >
      <span
        >{{
          breakdown.unknown_long_context_request_count
        }}
        条请求缺少长上下文判断事实，未对这些请求应用长上下文修正。</span
      >
    </div>
    <div
      v-if="breakdown.missing_model_request_count"
      class="alert text-sm alert-warning"
    >
      <span
        >{{
          breakdown.missing_model_request_count
        }}
        条请求没有模型名称，无法匹配特定模型规则。</span
      >
    </div>
  </div>
</template>
