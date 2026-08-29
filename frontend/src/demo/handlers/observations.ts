import type {
  FastCorrectionCalculateResult,
  FastCorrectionDetail,
  Observation,
  ObservationListData,
  ObservationRebuildResult,
} from "@/types/observations";

import type { DemoRequestContext } from "../backend";
import { demoIdentity, saveDemoState, type DemoState } from "../state";

function observationsData(context: DemoRequestContext): ObservationListData {
  const { state, url, paginate } = context;
  const accountId = Number(url.searchParams.get("account_id"));
  const account =
    state.monitoredAccounts.find((item) => item.id === accountId) ??
    state.monitoredAccounts.find((item) => item.enabled) ??
    state.monitoredAccounts[0];
  let items = [...state.observations].reverse().map((item) => ({
    ...item,
    account_id: account?.external_account_id ?? item.account_id,
  }));
  if (account?.provider === "cpa") items = [];
  const from = url.searchParams.get("from");
  const to = url.searchParams.get("to");
  const source = url.searchParams.get("source");
  const queryMode = url.searchParams.get("query_mode");
  if (from) items = items.filter((item) => item.observed_at >= from);
  if (to) items = items.filter((item) => item.observed_at <= to);
  if (source) items = items.filter((item) => item.source === source);
  if (queryMode) items = items.filter((item) => item.query_mode === queryMode);
  const page = paginate(items);
  return {
    ...page,
    account: account
      ? {
          id: account.id,
          provider: account.provider,
          source_account_id: account.source_account_id,
          external_account_id: account.external_account_id,
          name: account.name,
        }
      : null,
    fast_correction_enabled:
      account?.provider === "sub2api" &&
      Boolean(state.settings.fast_correction_enabled),
    summary: {
      total: items.length,
      valid_count: items.filter((item) => item.valid_sample).length,
      passive_count: items.filter((item) => item.query_mode === "passive")
        .length,
      excluded_count: items.filter((item) => item.excluded).length,
    },
  };
}

function fastCorrectionData(
  state: DemoState,
  observation: Observation,
): FastCorrectionDetail {
  const totalRequests = Math.max(
    12,
    Math.round((observation.delta_cost ?? 8) * 9),
  );
  const fastRequests = Math.round(totalRequests * 0.34);
  const fastCost = observation.fast_correction_usd ?? 0;
  return {
    observation_id: observation.id,
    started_at: observation.interval_cost_started_at,
    ended_at: observation.observed_at,
    calculated: true,
    cost_basis: "actual",
    cost_basis_label: "实际扣费",
    request_count: totalRequests,
    fast_request_count: fastRequests,
    non_fast_request_count: totalRequests - fastRequests,
    fast_billed_cost_usd: fastCost * 2,
    correction_usd: fastCost,
    corrected_fast_cost_usd: fastCost * 3,
    collection_error: "",
    users: state.participants.map((participant, index) => {
      const requestCount = Math.round(
        totalRequests * [0.39, 0.35, 0.26][index],
      );
      const participantFast = Math.round(requestCount * 0.34);
      const billed = fastCost * [0.39, 0.35, 0.26][index] * 2;
      return {
        sub2api_user_id: participant.sub2api_user_id,
        username: participant.sub2api_username,
        email: participant.sub2api_email,
        display_name: participant.name,
        request_count: requestCount,
        fast_request_count: participantFast,
        non_fast_request_count: requestCount - participantFast,
        fast_billed_cost_usd: billed,
        correction_usd: billed * 0.5,
        corrected_fast_cost_usd: billed * 1.5,
      };
    }),
  };
}

export function handleObservations(
  context: DemoRequestContext,
): Response | null {
  const { method, pathname, payload, state, ok, fail } = context;
  if (method === "GET" && pathname === "observations") {
    return ok(observationsData(context));
  }
  const fastCalculateMatch =
    /^observations\/(\d+)\/fast-correction\/calculate$/.exec(pathname);
  if (method === "POST" && fastCalculateMatch) {
    if (!demoIdentity()?.is_staff) return fail("没有管理员权限", 403);
    const observation = state.observations.find(
      (item) => item.id === Number(fastCalculateMatch[1]),
    );
    if (!observation) return fail("观测不存在", 404);
    if (!observation.fast_correction_calculated) {
      observation.fast_correction_usd = Number(
        (observation.selected_total_cost * 0.036).toFixed(6),
      );
      observation.fast_correction_calculated = true;
      saveDemoState(state);
    }
    return ok({
      observation_id: observation.id,
      fast_correction_usd: observation.fast_correction_usd ?? 0,
      fast_correction_calculated: true,
    } satisfies FastCorrectionCalculateResult);
  }
  const fastMatch = /^observations\/(\d+)\/fast-correction$/.exec(pathname);
  if (method === "GET" && fastMatch) {
    const observation = state.observations.find(
      (item) => item.id === Number(fastMatch[1]),
    );
    return observation
      ? ok(fastCorrectionData(state, observation))
      : fail("观测不存在", 404);
  }
  const exclusionMatch = /^observations\/(\d+)\/(exclude|restore)$/.exec(
    pathname,
  );
  if (method === "POST" && exclusionMatch) {
    const observation = state.observations.find(
      (item) => item.id === Number(exclusionMatch[1]),
    );
    if (!observation) return fail("观测不存在", 404);
    const excluded = exclusionMatch[2] === "exclude";
    observation.excluded = excluded;
    observation.valid_sample = !excluded;
    observation.excluded_at = excluded ? state.clock : null;
    observation.exclusion_reason = excluded
      ? String(payload.reason ?? "管理员手动排除")
      : "";
    observation.exclusion_source = excluded ? "manual" : "";
    saveDemoState(state);
    return ok({ replayed: true });
  }
  const manualStartMatch = /^observations\/(\d+)\/manual-start$/.exec(pathname);
  if (manualStartMatch && (method === "POST" || method === "DELETE")) {
    const observation = state.observations.find(
      (item) => item.id === Number(manualStartMatch[1]),
    );
    if (!observation) return fail("观测不存在", 404);
    if (method === "POST") {
      const endObservationId =
        typeof payload.end_observation_id === "number"
          ? payload.end_observation_id
          : observation.id;
      const endObservation = state.observations.find(
        (item) => item.id === endObservationId,
      );
      if (!endObservation) return fail("起点区间终点记录不存在", 404);
      const compare = (left: Observation, right: Observation) =>
        Date.parse(left.observed_at) - Date.parse(right.observed_at) ||
        left.id - right.id;
      if (
        observation.account_id !== endObservation.account_id ||
        compare(endObservation, observation) < 0
      ) {
        return fail("起点区间终点记录无效", 400);
      }
      for (const item of state.observations) {
        if (item.id === observation.id || !item.is_manual_start) continue;
        const existingEnd =
          state.observations.find(
            (candidate) => candidate.id === item.manual_start_end_id,
          ) ?? item;
        const overlaps =
          compare(item, endObservation) <= 0 &&
          compare(observation, existingEnd) <= 0;
        if (!overlaps) continue;
        const contained =
          compare(observation, item) <= 0 &&
          compare(existingEnd, endObservation) <= 0;
        if (!contained) return fail("起点区间与现有区间部分重叠", 400);
        item.is_manual_start = false;
        item.manual_start_reason = "";
        item.manual_start_set_at = null;
        item.manual_start_end_id = null;
        item.manual_start_end_observed_at = null;
      }
      observation.is_manual_start = true;
      observation.manual_start_reason = String(payload.reason ?? "");
      observation.manual_start_set_at = state.clock;
      observation.manual_start_end_id = endObservation.id;
      observation.manual_start_end_observed_at = endObservation.observed_at;
    } else {
      observation.is_manual_start = false;
      observation.manual_start_reason = "";
      observation.manual_start_set_at = null;
      observation.manual_start_end_id = null;
      observation.manual_start_end_observed_at = null;
    }
    saveDemoState(state);
    return ok({ replayed: true });
  }
  if (method === "POST" && pathname === "observations/rebuild") {
    const result: ObservationRebuildResult = {
      rebuilt_observations: state.observations.length,
      automatic_exclusions: state.observations.filter(
        (item) => item.exclusion_source === "automatic",
      ).length,
      inferred_intervals: 0,
      latest_observation_id: state.observations.at(-1)?.id ?? null,
      replay_started_at: state.periods[0].startedAt,
    };
    return ok(result);
  }
  return null;
}
