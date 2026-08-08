import type { BlockedIPAddress, BlockedIPSource } from "@/types";

export interface PendingBlockAction {
  mode: "block" | "unblock";
  address: string;
  sourceType: BlockedIPSource;
  sourceLabel: string;
  eventId: number | null;
  blockId: number | null;
}

export interface IPBlockDialogHandle {
  openBlock: (
    address: string,
    sourceType: BlockedIPSource,
    sourceLabel: string,
    eventId: number,
  ) => void;
  openUnblock: (item: BlockedIPAddress) => void;
  close: () => void;
}
