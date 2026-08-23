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
export interface CostBreakdown {
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
