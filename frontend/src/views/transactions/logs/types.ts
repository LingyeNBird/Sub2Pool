import type { SelectOption } from "@/types/common";
import type { NotificationRecord } from "@/types/security";

export type NotificationFilterKind =
  | "time"
  | "type"
  | "participant"
  | "subject"
  | "status";

export interface NotificationFilters {
  from: string;
  to: string;
  event_type: string;
  participant: string;
  subject: string;
  status: string;
}

export interface NotificationFilterOptions {
  types: SelectOption[];
  participants: { id: number; name: string }[];
  statuses: SelectOption[];
}

export interface NotificationFilterDialogHandle {
  open: (kind: NotificationFilterKind, filters: NotificationFilters) => void;
}

export interface NotificationDetailDialogHandle {
  open: (record: NotificationRecord) => void;
}
