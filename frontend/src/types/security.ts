import type { PagePermission } from "@/config/pagePermissions";
import type { PaginatedData, SelectOption } from "./common";

export interface SystemUser {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  page_permissions: PagePermission[];
  participant_ids: number[];
  participant_names: string[];
  account_ids: number[];
  account_names: string[];
  last_login: string | null;
  date_joined: string;
}
export interface AnnouncementRecord {
  code: string;
  title: string;
  published_at: string;
  severity: "info" | "warning";
  paragraphs: string[];
  read: boolean;
  read_at: string | null;
}
export interface AnnouncementListData {
  items: AnnouncementRecord[];
  unread_count: number;
}
export interface NotificationRecord {
  id: number;
  event_type: string;
  event_type_label: string;
  severity: string;
  participant_name: string | null;
  recipient: string;
  subject: string;
  body: string;
  status: string;
  status_label: string;
  error: string;
  created_at: string;
  sent_at: string | null;
}
export interface LoginEventRecord {
  id: number;
  username: string;
  success: boolean;
  request_ip: string | null;
  remote_ip: string | null;
  webrtc_supported: boolean | null;
  webrtc_ips: string[];
  user_agent: string;
  failure_reason: string;
  created_at: string;
}
export interface LoginEventData extends PaginatedData<LoginEventRecord> {
  success_count: number;
  failure_count: number;
  unique_request_ips: number;
}
export interface NotificationListData extends PaginatedData<NotificationRecord> {
  summary: {
    total: number;
    sent_count: number;
    failed_count: number;
  };
  filter_options: {
    types: SelectOption[];
    participants: { id: number; name: string }[];
    statuses: SelectOption[];
  };
}
export type BlockedIPSource = "request" | "remote" | "webrtc";
export interface BlockedIPAddress {
  id: number;
  address: string;
  source_type: BlockedIPSource;
  source_label: string;
  notes: string;
  login_event_id: number | null;
  created_at: string;
}
