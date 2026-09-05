import type { CorrectionBreakdown, CostBreakdown } from "@/types/common";

export function formatCurrency(value: number | null | undefined): string {
  return value == null ? "—" : `$${value.toFixed(2)}`;
}
export function correctionTotal(
  breakdown: CorrectionBreakdown | null | undefined,
): number | null {
  if (breakdown?.correction_total_usd !== undefined)
    return breakdown.correction_total_usd;
  return breakdown?.fast_correction_usd ?? null;
}

export function formatCorrectionCurrency(
  value: number | null | undefined,
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const amount = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  });
  return `${value < 0 ? "−" : value > 0 ? "+" : ""}$${amount}`;
}

export function formatCostTerms(
  total: number | null | undefined,
  breakdown: CostBreakdown | null | undefined,
  showCorrections: boolean,
): string {
  if (!breakdown || (!showCorrections && !correctionTotal(breakdown)))
    return formatCurrency(total);
  return `${formatCurrency(breakdown.sub2api_cost_usd)} + (${formatCorrectionCurrency(correctionTotal(breakdown))} 修正合计)`;
}

export function formatCostBreakdown(
  total: number | null | undefined,
  breakdown: CostBreakdown | null | undefined,
  showCorrections: boolean,
): string {
  const terms = formatCostTerms(total, breakdown, showCorrections);
  return breakdown && (showCorrections || correctionTotal(breakdown))
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
