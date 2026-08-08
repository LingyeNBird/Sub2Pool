import type { SystemUser } from "@/types";

export interface SystemUserFormData {
  username: string;
  email: string;
  password?: string;
  is_active: boolean;
  participant_ids: number[];
}

export interface SystemUserEditorHandle {
  open: (user: SystemUser | null) => void;
  close: () => void;
  showApiError: (error: unknown) => void;
}
