export interface PaginationMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}
export interface PaginatedData<T> {
  items: T[];
  pagination: PaginationMeta;
}
export interface SelectOption {
  value: string;
  label: string;
}
/** Additive fields also accept legacy FAST-only responses during upgrades. */
export interface CorrectionBreakdown {
  fast_correction_usd?: number | null;
  long_context_correction_usd?: number | null;
  model_correction_usd?: number | null;
  correction_total_usd?: number | null;
  correction_calculated?: boolean;
  correction_facts_complete?: boolean;
  legacy_fast_only?: boolean;
  missing_correction_intervals?: number;
  unknown_long_context_request_count?: number;
  missing_model_request_count?: number;
}
export interface CostBreakdown extends CorrectionBreakdown {
  sub2api_cost_usd: number;
  fast_correction_usd: number;
  total_cost_usd: number;
}
export type ConfirmDialogTone = "primary" | "warning" | "error";
export interface ConfirmDialogOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmDialogTone;
}
export interface ConfirmDialogHandle {
  open: (options: ConfirmDialogOptions) => Promise<boolean>;
  close: () => void;
}
