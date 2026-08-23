import type { Participant } from "@/types/participants";

import type { DemoRequestContext } from "../backend";
import { aggregateParticipant, saveDemoState, type DemoState } from "../state";

export function participantNames(state: DemoState, ids: number[]): string[] {
  return ids.flatMap((id) => {
    const participant = state.participants.find((item) => item.id === id);
    return participant ? [participant.name] : [];
  });
}

export function participantBreakdowns(
  state: DemoState,
  participantId: number,
  existing: Participant["account_breakdowns"] = [],
): Participant["account_breakdowns"] {
  return state.monitoredAccounts.map((account) => {
    const previous = existing.find((item) => item.account_id === account.id);
    const pool = state.quotaPools.find((item) => item.id === account.pool_id);
    const allocation = pool?.allocations.find(
      (item) => item.participant_id === participantId,
    );
    const contractShare = allocation?.share_percent ?? 0;
    const sourceUserId = state.participants.find(
      (item) => item.id === participantId,
    )?.sub2api_user_id;
    const snapshotCurrent =
      previous?.snapshot?.source_sub2api_user_id === sourceUserId;
    return {
      id: previous?.id ?? null,
      account_id: account.id,
      external_account_id: account.external_account_id,
      account_name: account.name,
      account_enabled: account.enabled,
      pool_id: account.pool_id,
      pool_name: pool?.name ?? `额度池 ${account.pool_id}`,
      contract_share_percent: contractShare,
      allocated: contractShare > 0,
      latest_selected_cost: previous?.latest_selected_cost ?? null,
      last_checked_at: previous?.last_checked_at ?? null,
      snapshot: snapshotCurrent ? (previous?.snapshot ?? null) : null,
    };
  });
}

export function handleParticipants({
  method,
  pathname,
  payload,
  state,
  ok,
  fail,
}: DemoRequestContext): Response | null {
  if (method === "GET" && pathname === "participants") {
    return ok(state.participants);
  }
  if (method === "GET" && pathname === "participants/sub2api-users") {
    return ok(state.sub2apiUsers);
  }
  if (method === "POST" && pathname === "participants") {
    const id = state.nextParticipantId++;
    const participant: Participant = {
      id,
      name: String(payload.name ?? `演示参与者 ${id}`),
      email: String(payload.email ?? `participant-${id}@example.test`),
      sub2api_user_id: Number(payload.sub2api_user_id ?? 100 + id),
      sub2api_username: String(payload.sub2api_username ?? `demo-user-${id}`),
      sub2api_email: String(
        payload.sub2api_email ?? `participant-${id}@example.test`,
      ),
      sub2api_identity: String(payload.sub2api_username ?? `demo-user-${id}`),
      pool_allocations: [],
      is_owner: Boolean(payload.is_owner),
      enabled: payload.enabled !== false,
      notes: String(payload.notes ?? ""),
      latest_balance_usd: null,
      last_checked_at: null,
      account_breakdowns: [],
      snapshot: null,
    };
    participant.account_breakdowns = participantBreakdowns(state, id);
    aggregateParticipant(participant);
    state.participants.push(participant);
    saveDemoState(state);
    return ok(participant, 201);
  }
  const participantMatch = /^participants\/(\d+)$/.exec(pathname);
  if (participantMatch && method === "PUT") {
    const participant = state.participants.find(
      (item) => item.id === Number(participantMatch[1]),
    );
    if (!participant) return fail("参与者不存在", 404);
    Object.assign(participant, {
      name: String(payload.name ?? participant.name),
      email: String(payload.email ?? participant.email),
      sub2api_user_id: Number(
        payload.sub2api_user_id ?? participant.sub2api_user_id,
      ),
      sub2api_username: String(
        payload.sub2api_username ?? participant.sub2api_username,
      ),
      sub2api_email: String(payload.sub2api_email ?? participant.sub2api_email),
      sub2api_identity: String(
        payload.sub2api_username ?? participant.sub2api_username,
      ),
      is_owner: Boolean(payload.is_owner ?? participant.is_owner),
      enabled: Boolean(payload.enabled ?? participant.enabled),
      notes: String(payload.notes ?? participant.notes),
    });
    participant.account_breakdowns = participantBreakdowns(
      state,
      participant.id,
      participant.account_breakdowns,
    );
    aggregateParticipant(participant);
    saveDemoState(state);
    return ok(participant);
  }
  if (participantMatch && method === "DELETE") {
    const id = Number(participantMatch[1]);
    const participant = state.participants.find((item) => item.id === id);
    if (!participant) return fail("参与者不存在", 404);
    participant.enabled = false;
    for (const user of state.systemUsers) {
      user.participant_ids = user.participant_ids.filter(
        (participantId) => participantId !== id,
      );
      user.participant_names = participantNames(state, user.participant_ids);
    }
    saveDemoState(state);
    return ok({ disabled: true });
  }
  return null;
}
