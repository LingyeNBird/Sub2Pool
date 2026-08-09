import type { CostBreakdown } from "@/types";

export function formatCurrency(value: number | null | undefined): string {
  return value == null ? "—" : `$${value.toFixed(2)}`;
}
export function formatCostTerms(
  total: number | null | undefined,
  breakdown: CostBreakdown | null | undefined,
  showFastCorrection: boolean,
): string {
  if (!showFastCorrection || !breakdown) return formatCurrency(total);
  return `${formatCurrency(breakdown.sub2api_cost_usd)} + ${formatCurrency(
    breakdown.fast_correction_usd,
  )} FAST`;
}

export function formatCostBreakdown(
  total: number | null | undefined,
  breakdown: CostBreakdown | null | undefined,
  showFastCorrection: boolean,
): string {
  const terms = formatCostTerms(total, breakdown, showFastCorrection);
  return showFastCorrection && breakdown
    ? `${terms} = ${formatCurrency(breakdown.total_cost_usd)}`
    : terms;
}

export function formatCurrencyRange(
  minimum: number | null | undefined,
  maximum: number | null | undefined,
  fallback: number | null | undefined,
): string {
  if (minimum != null && maximum != null) {
    return `${formatCurrency(minimum)} ~ ${formatCurrency(maximum)}`;
  }
  return formatCurrency(fallback);
}

export function formatPercent(value: number | null | undefined): string {
  return value == null ? "—" : `${value.toFixed(2)}%`;
}

export function formatCompactPercent(value: number | null | undefined): string {
  return value == null ? "—" : `${Number(value.toFixed(2))}%`;
}
