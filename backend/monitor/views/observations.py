"""观测记录查询、FAST 明细与人工重放控制接口。"""

from decimal import Decimal

from django.db.models import Q
from django.shortcuts import get_object_or_404

from .base import AdminAPIView, error, ok
from ..reporting import iso, snapshot_data
from .record_helpers import paginated_rows, query_datetime
from ..fast_correction.constants import (
    FAST_EXTRA_FACTOR,
    SUB2API_FAST_MULTIPLIER,
    UPSTREAM_FAST_MULTIPLIER,
)
from ..models import (
    AppSettings,
    Observation,
    Participant,
    Sub2APIUserUsageSample,
)
from ..replay import (
    clear_manual_start,
    exclude_observation,
    rebuild_current_interval,
    restore_observation,
    set_manual_start,
)


class ObservationListView(AdminAPIView):
    def get(self, request):
        config = AppSettings.load()
        queryset = Observation.objects.prefetch_related(
            "participant_snapshots__participant"
        )
        try:
            observed_from = query_datetime(request, "from")
            observed_to = query_datetime(request, "to")
        except ValueError as exc:
            return error(str(exc), 400)
        if observed_from:
            queryset = queryset.filter(observed_at__gte=observed_from)
        if observed_to:
            queryset = queryset.filter(observed_at__lte=observed_to)

        source = request.query_params.get("source")
        valid_sources = {value for value, _label in Observation.SOURCE_CHOICES}
        if source:
            if source not in valid_sources:
                return error("来源筛选值无效", 400)
            queryset = queryset.filter(source=source)

        query_mode = request.query_params.get("query_mode")
        if query_mode:
            if query_mode not in {"passive", "direct"}:
                return error("查询方式筛选值无效", 400)
            if query_mode == "passive":
                queryset = queryset.filter(raw_window__query_mode="passive")
            else:
                # 旧观测尚未写 query_mode 时，展示层一直将它解释为上游直查。
                queryset = queryset.filter(
                    Q(raw_window__query_mode="direct")
                    | ~Q(raw_window__has_key="query_mode")
                )

        total = queryset.count()
        excluded_count = queryset.filter(excluded_at__isnull=False).count()
        valid_count = queryset.filter(valid_sample=True).count()
        passive_count = queryset.filter(raw_window__query_mode="passive").count()
        rows, pagination = paginated_rows(request, queryset)
        result = []
        for item in rows:
            result.append(
                {
                    "id": item.id,
                    "observed_at": iso(item.observed_at),
                    "source": item.source,
                    "account_id": item.account_id,
                    "attribution_started_at": iso(item.attribution_started_at),
                    "upstream_resets_at": iso(item.upstream_resets_at),
                    "upstream_used_percent": float(item.upstream_used_percent),
                    "interval_used_percent": float(item.interval_used_percent),
                    "raw_selected_total_cost": float(
                        item.raw_selected_total_cost
                    ),
                    "selected_total_cost": float(item.selected_total_cost),
                    "cost_window_started_at": iso(
                        item.cost_window_started_at
                    ),
                    "cost_window_ended_at": iso(item.cost_window_ended_at),
                    "interval_cost_started_at": iso(
                        item.interval_cost_started_at
                    ),
                    "interval_cost": (
                        float(item.interval_cost(config.cost_basis))
                        if item.interval_cost(config.cost_basis) is not None
                        else None
                    ),
                    "interval_cost_source": item.interval_cost_source,
                    "normalized_total_cost": float(
                        item.normalized_cost(config.cost_basis)
                    ),
                    "delta_percent": (
                        float(item.delta_percent)
                        if item.delta_percent is not None
                        else None
                    ),
                    "delta_cost": (
                        float(item.delta_cost)
                        if item.delta_cost is not None
                        else None
                    ),
                    "sample_usd_per_percent": (
                        float(item.sample_usd_per_percent)
                        if item.sample_usd_per_percent is not None
                        else None
                    ),
                    "effective_usd_per_percent": float(
                        item.effective_usd_per_percent
                    ),
                    "estimated_used_percent": float(
                        item.estimated_used_percent
                    ),
                    "capacity_lower_usd": (
                        float(item.capacity_lower_usd)
                        if item.capacity_lower_usd is not None
                        else None
                    ),
                    "capacity_upper_usd": (
                        float(item.capacity_upper_usd)
                        if item.capacity_upper_usd is not None
                        else None
                    ),
                    "model_diagnostics": item.model_diagnostics,
                    "fast_correction_usd": (
                        float(
                            item.fast_correction_actual_cost
                            if config.cost_basis == "actual"
                            else item.fast_correction_standard_cost
                        )
                        if (
                            item.fast_correction_actual_cost
                            if config.cost_basis == "actual"
                            else item.fast_correction_standard_cost
                        )
                        is not None
                        else None
                    ),
                    "fast_correction_calculated": (
                        item.fast_correction_standard_cost is not None
                        and item.fast_correction_actual_cost is not None
                    ),
                    "valid_sample": item.valid_sample,
                    "sample_note": item.sample_note,
                    "rate_method": item.raw_window.get(
                        "rate_method",
                        "incremental_legacy",
                    ),
                    "query_mode": item.raw_window.get("query_mode", "direct"),
                    "snapshot_sampled_at": item.raw_window.get("sampled_at"),
                    "excluded": item.excluded_at is not None,
                    "excluded_at": iso(item.excluded_at),
                    "exclusion_reason": item.exclusion_reason,
                    "exclusion_source": item.exclusion_source,
                    "is_manual_start": item.is_manual_start,
                    "manual_start_reason": item.manual_start_reason,
                    "manual_start_set_at": iso(item.manual_start_set_at),
                    "participants": [
                        snapshot_data(snapshot)
                        for snapshot in item.participant_snapshots.all()
                    ],
                }
            )
        return ok(
            {
                "items": result,
                "fast_correction_enabled": config.fast_correction_enabled,
                "pagination": pagination,
                "summary": {
                    "total": total,
                    "valid_count": valid_count,
                    "passive_count": passive_count,
                    "excluded_count": excluded_count,
                },
            }
        )


class ObservationFastCorrectionDetailView(AdminAPIView):
    """展示一个采样区间内已持久化的 FAST 修正事实。"""

    def get(self, _request, observation_id: int):
        config = AppSettings.load()
        observation = get_object_or_404(
            Observation.objects.prefetch_related("fast_corrections"),
            pk=observation_id,
        )
        details = list(observation.fast_corrections.all())
        user_ids = [item.sub2api_user_id for item in details]
        user_samples = {
            item.sub2api_user_id: item
            for item in Sub2APIUserUsageSample.objects.filter(
                account_id=observation.account_id,
                observed_at=observation.observed_at,
                sub2api_user_id__in=user_ids,
            )
        }
        participants = {
            item.sub2api_user_id: item
            for item in Participant.objects.filter(
                sub2api_user_id__in=user_ids,
            )
        }
        fast_cost_field = (
            "fast_actual_cost"
            if config.cost_basis == "actual"
            else "fast_standard_cost"
        )
        correction_field = (
            "actual_correction_cost"
            if config.cost_basis == "actual"
            else "standard_correction_cost"
        )
        selected_correction = (
            observation.fast_correction_actual_cost
            if config.cost_basis == "actual"
            else observation.fast_correction_standard_cost
        )
        fast_request_count = sum(
            item.fast_request_count for item in details
        )
        request_count = observation.fast_correction_request_count
        fast_billed_cost = sum(
            (getattr(item, fast_cost_field) for item in details),
            Decimal("0"),
        )
        correction = selected_correction or Decimal("0")

        users = []
        for item in details:
            sample = user_samples.get(item.sub2api_user_id)
            participant = participants.get(item.sub2api_user_id)
            username = sample.username if sample else ""
            email = sample.email if sample else ""
            user_fast_cost = getattr(item, fast_cost_field)
            user_correction = getattr(item, correction_field)
            users.append(
                {
                    "sub2api_user_id": item.sub2api_user_id,
                    "username": username,
                    "email": email,
                    "display_name": (
                        username
                        or email
                        or (participant.name if participant else "")
                        or f"用户 {item.sub2api_user_id}"
                    ),
                    "request_count": item.request_count,
                    "fast_request_count": item.fast_request_count,
                    "non_fast_request_count": (
                        max(0, item.request_count - item.fast_request_count)
                        if item.request_count is not None
                        else None
                    ),
                    "fast_billed_cost_usd": float(user_fast_cost),
                    "correction_usd": float(user_correction),
                    "corrected_fast_cost_usd": float(
                        user_fast_cost + user_correction
                    ),
                }
            )

        return ok(
            {
                "observation_id": observation.id,
                "started_at": iso(observation.fast_correction_started_at),
                "ended_at": iso(observation.observed_at),
                "calculated": (
                    observation.fast_correction_standard_cost is not None
                    and observation.fast_correction_actual_cost is not None
                ),
                "cost_basis": config.cost_basis,
                "cost_basis_label": (
                    "实际扣费" if config.cost_basis == "actual" else "标准扣费"
                ),
                "request_count": request_count,
                "fast_request_count": fast_request_count,
                "non_fast_request_count": (
                    max(0, request_count - fast_request_count)
                    if request_count is not None
                    else None
                ),
                "fast_billed_cost_usd": float(fast_billed_cost),
                "correction_usd": float(correction),
                "corrected_fast_cost_usd": float(
                    fast_billed_cost + correction
                ),
                "sub2api_fast_multiplier": float(SUB2API_FAST_MULTIPLIER),
                "upstream_fast_multiplier": float(
                    UPSTREAM_FAST_MULTIPLIER
                ),
                "correction_ratio": float(FAST_EXTRA_FACTOR),
                "collection_error": str(
                    observation.raw_window.get("fast_correction_error") or ""
                ),
                "users": users,
            }
        )


class ObservationRebuildView(AdminAPIView):
    """保留原始采样与人工标记，仅重建当前区间的全部派生结论。"""

    def post(self, _request):
        config = AppSettings.load()
        if not config.openai_account_id:
            return error("尚未配置 OpenAI 上游账号", 400)
        replay, replay_from = rebuild_current_interval(
            config.openai_account_id,
            config,
        )
        return ok(
            {
                **replay.as_dict(),
                "replay_started_at": iso(replay_from),
            }
        )


class ObservationExclusionView(AdminAPIView):
    """排除一条校准记录，并从最早受影响区间向后重放。"""

    def post(self, request, observation_id: int):
        observation = get_object_or_404(Observation, pk=observation_id)
        reason = request.data.get("reason", "管理员手动排除")
        if not isinstance(reason, str):
            return error("排除原因格式无效", 400)
        result = exclude_observation(observation, reason)
        return ok(result)


class ObservationRestoreView(AdminAPIView):
    """恢复一条排除记录，并从最早受影响区间向后重放。"""

    def post(self, _request, observation_id: int):
        observation = get_object_or_404(Observation, pk=observation_id)
        return ok(restore_observation(observation))


class ObservationManualStartView(AdminAPIView):
    """设置或取消最高优先级的管理员观测起点。"""

    def post(self, request, observation_id: int):
        observation = get_object_or_404(Observation, pk=observation_id)
        reason = request.data.get("reason", "")
        if not isinstance(reason, str):
            return error("起点说明格式无效", 400)
        return ok(set_manual_start(observation, reason))

    def delete(self, _request, observation_id: int):
        observation = get_object_or_404(Observation, pk=observation_id)
        return ok(clear_manual_start(observation))
