<script setup lang="ts">
import type { CostBreakdown } from "@/types/common";
import { formatCurrency } from "@/utils/formatters";
import CorrectionAmount from "./CorrectionAmount.vue";
withDefaults(
  defineProps<{
    total: number | null | undefined;
    breakdown?: CostBreakdown | null;
    showCorrections?: boolean;
    termsOnly?: boolean;
  }>(),
  { showCorrections: true, termsOnly: false },
);
</script>

<template>
  <span v-if="breakdown && showCorrections">
    {{ formatCurrency(breakdown.sub2api_cost_usd) }} + (<CorrectionAmount
      :breakdown="breakdown"
      label="修正合计 "
    />)
    <template v-if="!termsOnly">
      = {{ formatCurrency(breakdown.total_cost_usd) }}</template
    >
  </span>
  <span v-else>{{ formatCurrency(total) }}</span>
</template>
