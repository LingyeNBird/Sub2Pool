import type { PagePermission } from "@/config/pagePermissions";
import type { MonitoredAccount } from "@/types/accounts";
import type { Observation } from "@/types/observations";
import type {
  Participant,
  QuotaPoolAllocation,
  Sub2APIUserOption,
} from "@/types/participants";
import type {
  ParticleTrajectoryData,
  ParticleTrajectoryPoint,
} from "@/types/particleTrajectory";
import type {
  BlockedIPAddress,
  LoginEventRecord,
  NotificationRecord,
  SystemUser,
} from "@/types/security";
import type { AppSettingsData, HistoricalRebuildPlan } from "@/types/settings";

import { initializeState } from "./fixtures";

export { aggregateParticipant } from "./participantProjection";
export {
  apiUsageData,
  dashboardData,
  participantUsagePoints,
  periodSummary,
  trajectoryData,
} from "./selectors";

export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "true";

const DEMO_STATE_KEY = "sub2pool:demo:v8:state";
const DEMO_AUTH_KEY = "sub2pool:demo:v2:auth";

export interface DemoAuthIdentity {
  username: string;
  is_staff: boolean;
  page_permissions: PagePermission[];
  timezone: string;
}

export interface DemoPeriod {
  id: number;
  sequence: number;
  startedAt: string;
  resetsAt: string;
  endedAt: string;
  observationIds: number[];
  trajectory: ParticleTrajectoryPoint[];
  promotions: ParticleTrajectoryData["promotions"];
}

export interface DemoState {
  version: 15;
  clock: string;
  nextParticipantId: number;
  nextPoolId: number;
  nextSystemUserId: number;
  nextObservationId: number;
  nextBlockedId: number;
  revision: number;
  participants: Participant[];
  monitoredAccounts: MonitoredAccount[];
  quotaPools: QuotaPoolAllocation[];
  sub2apiUsers: Sub2APIUserOption[];
  systemUsers: SystemUser[];
  observations: Observation[];
  periods: DemoPeriod[];
  notifications: NotificationRecord[];
  loginEvents: LoginEventRecord[];
  blockedAddresses: BlockedIPAddress[];
  announcementReads: string[];
  settings: AppSettingsData;
  plans: HistoricalRebuildPlan[];
}

export function loadDemoState(): DemoState {
  const stored = sessionStorage.getItem(DEMO_STATE_KEY);
  if (stored) {
    try {
      const parsed = JSON.parse(stored) as DemoState;
      if (parsed.version === 15) return parsed;
    } catch {
      sessionStorage.removeItem(DEMO_STATE_KEY);
    }
  }
  const initial = initializeState();
  saveDemoState(initial);
  return initial;
}

export function saveDemoState(state: DemoState): void {
  sessionStorage.setItem(DEMO_STATE_KEY, JSON.stringify(state));
}

export function resetDemoState(): DemoState {
  sessionStorage.removeItem(DEMO_STATE_KEY);
  const state = initializeState();
  saveDemoState(state);
  window.dispatchEvent(new CustomEvent("sub2pool:demo-reset"));
  return state;
}

export function demoIdentity(): DemoAuthIdentity | null {
  const stored = sessionStorage.getItem(DEMO_AUTH_KEY);
  if (!stored) return null;
  try {
    const identity = JSON.parse(stored) as DemoAuthIdentity;
    return identity.username ? identity : null;
  } catch {
    return null;
  }
}

export function setDemoIdentity(identity: DemoAuthIdentity): void {
  sessionStorage.setItem(DEMO_AUTH_KEY, JSON.stringify(identity));
}

export function clearDemoIdentity(): void {
  sessionStorage.removeItem(DEMO_AUTH_KEY);
}
