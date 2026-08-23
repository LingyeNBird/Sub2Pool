import type { PagePermission } from "@/config/pagePermissions";
import type { SystemUser } from "@/types/security";

export interface SystemUserFormData {
  username: string;
  email: string;
  password?: string;
  is_active: boolean;
}

export interface SystemUserPermissionFormData {
  page_permissions: PagePermission[];
  participant_ids: number[];
  account_ids: number[];
}

export interface SystemUserEditorHandle {
  open: (user: SystemUser | null) => void;
  close: () => void;
  showApiError: (error: unknown) => void;
}

export interface SystemUserPermissionEditorHandle {
  open: (user: SystemUser) => void;
  close: () => void;
  showApiError: (error: unknown) => void;
}
