export function formatCurrency(value: number | null | undefined): string {
  return value == null ? "—" : `$${value.toFixed(2)}`;
}

export function formatPercent(value: number | null | undefined): string {
  return value == null ? "—" : `${value.toFixed(2)}%`;
}

export function formatCompactPercent(value: number | null | undefined): string {
  return value == null ? "—" : `${Number(value.toFixed(2))}%`;
}
