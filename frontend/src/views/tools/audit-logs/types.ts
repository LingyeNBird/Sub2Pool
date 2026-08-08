export type ObservationFilterKind = "time" | "source" | "query";

export interface ObservationFilters {
  from: string;
  to: string;
  source: string;
  query_mode: string;
}

export interface ObservationSummary {
  total: number;
  valid_count: number;
  passive_count: number;
  excluded_count: number;
}

export interface DialogController<T extends unknown[] = []> {
  open: (...args: T) => void;
  close: () => void;
}
