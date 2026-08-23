import type { Participant } from "@/types";

export type ParticipantViewMode = "cards" | "table";

export interface ParticipantFormData {
  name: string;
  email: string;
  sub2api_user_id: number;
  sub2api_username: string;
  sub2api_email: string;
  is_owner: boolean;
  enabled: boolean;
  notes: string;
}

export interface ParticipantEditorHandle {
  open: (participant: Participant | null) => void;
  close: () => void;
}
