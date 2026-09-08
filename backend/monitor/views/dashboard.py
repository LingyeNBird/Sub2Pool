"""Dashboard and global participant-balance operations."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.http import Http404
from django.utils import timezone

from .base import AdminAPIView, PageAccessAPIView, error, ok
from ..api_auth import APIKeyAuthentication
from ..access import (
    scope_participant_data,
    visible_account_ids,
    visible_accounts_for,
    visible_participant_ids,
)
from ..balance_operations import BalanceApplicationError, apply_participant_recommendation
from ..history_state import LeaseBusyError
from ..models import (
    AppSettings,
    MonitoredAccount,
    Observation,
    PagePermission,
    Participant,
)
from ..reporting import (
    FastCorrectionBreakdownPresenter,
    display_cycle_rates,
    iso,
    participant_data,
)


def _admin_url(value: str) -> str:
    """Expose only the configured origin, never an administrative path."""
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = f"{hostname}:{parsed.port}" if parsed.port is not None else hostname
    return f"{parsed.scheme}://{authority}"


def _account_data(account: MonitoredAccount) -> dict:
    return {
        "id": account.id,
        "provider": account.provider,
        "source_account_id": account.source_account_id,
        "external_account_id": account.external_account_id,
        "name": account.name,
        "enabled": account.enabled,
        "quota_query_mode": account.quota_query_mode,
        "last_local_check_at": iso(account.last_local_check_at),
        "last_upstream_check_at": iso(account.last_upstream_check_at),
        "last_success_at": iso(account.last_success_at),
        "next_local_check_at": iso(account.next_local_check_at),
        "last_error": account.last_error,
    }


def _selected_account(
    request,
) -> tuple[list[MonitoredAccount], MonitoredAccount | None]:
    accounts = list(
        visible_accounts_for(
            request.user,
            MonitoredAccount.objects.order_by(
                "name",
                "provider",
                "external_account_id",
                "cpa_auth_index",
            ),
        )
    )
    raw_account_id = request.query_params.get("account_id")
    if raw_account_id is None:
        selected = next((item for item in accounts if item.enabled), None)
        return accounts, selected
    try:
        account_id = int(raw_account_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("监控账号参数无效") from exc
    selected = next((item for item in accounts if item.id == account_id), None)
    if selected is None:
        raise ValueError("监控账号不存在或未授权")
    return accounts, selected

def _participant_rows(
    config: AppSettings,
    user,
) -> tuple[list[dict], list[dict]]:
    all_rows = [
        participant_data(item, config)
        for item in Participant.objects.filter(enabled=True).prefetch_related(
            "account_memberships__account"
        )
    ]
    visible_participant_id_set = visible_participant_ids(user)
    allowed_account_ids = visible_account_ids(user)
    visible_rows = [
        scope_participant_data(item, allowed_account_ids)
        for item in all_rows
        if visible_participant_id_set is None
        or item["id"] in visible_participant_id_set
    ]
    return all_rows, visible_rows


def _actionable_recommendations(participant_rows: list[dict]) -> list[dict]:
    return [
        item
        for item in participant_rows
        if item["snapshot"]
        and item["snapshot"]["needs_manual_update"]
        and not item["snapshot"]["recommendation_applied"]
    ]



class DashboardView(PageAccessAPIView):
    required_page_permissions = (PagePermission.DASHBOARD,)

    def get(self, request):
        config = AppSettings.load()
        try:
            accounts, account = _selected_account(request)
        except ValueError as exc:
            return error(str(exc), 400)
        fact_account_id = account.fact_key if account else None
        cost_breakdowns = FastCorrectionBreakdownPresenter(
            config,
            fact_account_id,
        )
        snapshot_stale = bool(
            account
            and account.last_upstream_check_at
            and timezone.now() - account.last_upstream_check_at
            >= timedelta(hours=config.stale_warning_hours)
        )
        observation = (
            Observation.objects.filter(
                account_id=fact_account_id,
                excluded_at__isnull=True,
                attribution_started_at__isnull=False,
            )
            .prefetch_related("participant_snapshots__participant")
            .order_by("-observed_at", "-id")
            .first()
            if fact_account_id is not None
            else None
        )
        all_participant_rows, participant_rows = (
            _participant_rows(config, request.user)
            if account is None or account.provider == "sub2api"
            else ([], [])
        )
        selected_snapshots = []
        if account is not None:
            for participant in all_participant_rows:
                breakdown = next(
                    (
                        item
                        for item in participant["account_breakdowns"]
                        if item["account_id"] == account.id
                    ),
                    None,
                )
                if breakdown and breakdown["snapshot"]:
                    selected_snapshots.append(breakdown["snapshot"])
        total_charged = sum(
            (
                Decimal(str(item["charged_cycle_percent"]))
                for item in selected_snapshots
            ),
            Decimal("0"),
        )
        display_rate, raw_rate = (
            display_cycle_rates(observation, config)
            if observation
            else (None, None)
        )
        presented_estimated_percent = (
            observation.interval_used_percent
            if observation is not None
            and config.weekly_quota_model == "constant_average"
            else (
                observation.estimated_used_percent
                if observation is not None
                else Decimal("0")
            )
        )
        unattributed_used_percent = Decimal("0")
        if observation is not None:
            residual = observation.model_diagnostics.get(
                "residual_attributed_percent"
            )
            if config.weekly_quota_model == "time_varying" and residual is not None:
                unattributed_used_percent = Decimal(str(residual))
            else:
                unattributed_used_percent = max(
                    Decimal("0"),
                    presented_estimated_percent - total_charged,
                )

        actionable = (
            _actionable_recommendations(participant_rows)
            if account is None or account.provider == "sub2api"
            else []
        )
        data = {
            "configured": bool(
                account
                and (
                    config.cpa_management_key_encrypted
                    if account.provider == "cpa"
                    else config.sub2api_admin_token_encrypted
                )
            ),
            "monitoring_enabled": config.monitoring_enabled,
            "accounts": [_account_data(item) for item in accounts],
            "selected_account_id": account.id if account else None,
            "selected_provider": account.provider if account else None,
            "last_local_check_at": iso(
                account.last_local_check_at if account else config.last_local_check_at
            ),
            "last_upstream_check_at": iso(
                account.last_upstream_check_at
                if account
                else config.last_upstream_check_at
            ),
            "snapshot_stale": snapshot_stale,
            "last_success_at": iso(
                account.last_success_at if account else config.last_success_at
            ),
            "last_error": account.last_error if account else config.last_error,
            "quota_query_mode": account.quota_query_mode if account else None,
            "sub2api_admin_url": _admin_url(config.sub2api_base_url),
            "upstream_admin_url": _admin_url(
                config.cpa_base_url
                if account is not None and account.provider == "cpa"
                else config.sub2api_base_url
            ),
            "fast_correction_enabled": bool(
                config.fast_correction_enabled
                and account is not None
                and account.provider == "sub2api"
            ),
            "weekly_quota_model": config.weekly_quota_model,
            "cycle": None,
            "participants": actionable,
            "needs_manual_update_count": len(actionable),
        }
        if observation is not None:
            data["cycle"] = {
                "id": observation.id,
                "observed_at": iso(observation.observed_at),
                "starts_at": iso(observation.attribution_started_at),
                "resets_at": iso(observation.upstream_resets_at),
                "upstream_used_percent": float(observation.upstream_used_percent),
                "interval_used_percent": float(observation.interval_used_percent),
                "effective_usd_per_percent": (
                    float(display_rate) if display_rate is not None else None
                ),
                "selected_total_cost": float(observation.selected_total_cost),
                "selected_total_cost_breakdown": (
                    cost_breakdowns.for_observation(observation)
                ),
                "start_cost_breakdown": cost_breakdowns.zero(),
                "unattributed_used_percent": float(unattributed_used_percent),
                "sample_note": observation.sample_note,
                "snapshot_sampled_at": observation.raw_window.get("sampled_at"),
                "rate_calculated": (
                    raw_rate is not None
                    if config.weekly_quota_model == "constant_average"
                    else bool(observation.model_diagnostics)
                ),
                "estimated_used_percent": float(presented_estimated_percent),
                "capacity_lower_usd": (
                    float(observation.capacity_lower_usd)
                    if observation.capacity_lower_usd is not None
                    else None
                ),
                "capacity_upper_usd": (
                    float(observation.capacity_upper_usd)
                    if observation.capacity_upper_usd is not None
                    else None
                ),
                "model_diagnostics": observation.model_diagnostics,
            }
        return ok(data)


class ReadOnlyDashboardView(DashboardView):
    """External API-key view exposing the dashboard summary."""

    authentication_classes = [APIKeyAuthentication]
    http_method_names = ["get", "head", "options"]


class ReadOnlyRecommendationListView(PageAccessAPIView):
    """External API-key view exposing homepage recommendations pending application."""

    required_page_permissions = (PagePermission.DASHBOARD,)
    authentication_classes = [APIKeyAuthentication]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        _, participant_rows = _participant_rows(AppSettings.load(), request.user)
        return ok(_actionable_recommendations(participant_rows))




class ApplyParticipantRecommendationView(AdminAPIView):
    """Apply one aggregate recommendation through an idempotent journal."""

    def post(self, _request, participant_id: int):
        try:
            operation = apply_participant_recommendation(participant_id)
        except Participant.DoesNotExist as exc:
            raise Http404("No Participant matches the given query.") from exc
        except LeaseBusyError as exc:
            return error(str(exc), 409)
        except BalanceApplicationError as exc:
            return error(str(exc), exc.status_code, exc.details)
        return ok(
            {
                "operation_id": str(operation.id),
                "participant_id": operation.participant_id,
                "sub2api_user_id": operation.sub2api_user_id,
                "applied_balance_usd": float(operation.confirmed_balance_usd),
                "account_count": operation.sources.count(),
            }
        )


class APIApplyParticipantRecommendationView(ApplyParticipantRecommendationView):
    """Apply a recommendation with the administrator-issued external API key."""

    authentication_classes = [APIKeyAuthentication]
