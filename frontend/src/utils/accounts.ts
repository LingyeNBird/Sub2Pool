import type { MonitoredAccount } from "@/types/accounts";

export function monitoredAccountLabel(account: MonitoredAccount): string {
  return account.provider === "cpa" ? `${account.name} · CPA` : account.name;
}
