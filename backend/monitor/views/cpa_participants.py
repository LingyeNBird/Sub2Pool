"""CPA carpool API. Administrative identity writes and scoped reads stay separate."""

from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers

from ..api_auth import APIKeyAuthentication
from ..cpa.participants import (
    apply_claim,
    bind_key,
    ownership_filter,
    preview_claim,
    unbind_key,
)
from ..cpa.reporting import pool_summary
from ..cpa.request_summary import request_summary
from ..cpa.usage import cpa_event_cost
from ..models import (
    AppSettings,
    CPAAPIKey,
    CPAClaimPlan,
    CPAKeyBinding,
    CPAUsageEvent,
    PagePermission,
    Participant,
)
from .base import AdminAPIView, PageAccessAPIView, error, ok
from .query_params import monitored_account_query


class CPAErrorEnvelope:
    def handle_exception(self, exc):
        from ..history_state import LeaseBusyError

        if isinstance(exc, LeaseBusyError):
            return error("账号正在采集或重算，请稍后重试", 409)
        response = super().handle_exception(exc)

        def first_message(value):
            if isinstance(value, dict):
                return first_message(next(iter(value.values()), "请求失败"))
            if isinstance(value, (list, tuple)):
                return first_message(value[0]) if value else "请求失败"
            return str(value)

        result = error(
            first_message(response.data), response.status_code, response.data
        )
        result.exception = True
        for name, value in response.items():
            result[name] = value
        return result


class CPAAdminView(CPAErrorEnvelope, AdminAPIView):
    pass


class CPAReadView(CPAErrorEnvelope, PageAccessAPIView):
    pass


def cpa_account(request, *, enabled_only=True):
    if not request.query_params.get("account_id"):
        raise serializers.ValidationError("必须指定 account_id")
    try:
        account = monitored_account_query(request, enabled_only=enabled_only)
    except ValueError as exc:
        raise serializers.ValidationError(str(exc)) from exc
    if account is None or account.provider != "cpa":
        raise serializers.ValidationError("请选择已授权的 CPA 账号")
    return account


def binding_data(binding):
    return {
        "id": binding.id,
        "key_id": binding.key_id,
        "hint": binding.key.hint,
        "name": binding.key.name,
        "participant_id": binding.participant_id,
        "participant_name": binding.participant.name,
        "started_at": binding.started_at.isoformat(),
        "ended_at": binding.ended_at.isoformat() if binding.ended_at else None,
    }


def claim_data(plan):
    return {
        "id": str(plan.id),
        "key_id": plan.key_id,
        "participant_id": plan.participant_id,
        "started_at": plan.started_at.isoformat(),
        "ended_at": plan.ended_at.isoformat(),
        "expires_at": plan.expires_at.isoformat(),
        "applied_at": plan.applied_at.isoformat() if plan.applied_at else None,
        **plan.preview,
    }


class BindingWrite(serializers.Serializer):
    participant_id = serializers.PrimaryKeyRelatedField(
        queryset=Participant.objects.filter(enabled=True), source="participant"
    )
    raw_key = serializers.CharField(
        required=False, allow_blank=True, max_length=4096, write_only=True
    )
    observed_hash = serializers.RegexField(r"^[a-f0-9]{64}$", required=False)
    name = serializers.CharField(required=False, allow_blank=True, max_length=80)

    def validate(self, attrs):
        if bool(attrs.get("raw_key")) == bool(attrs.get("observed_hash")):
            raise serializers.ValidationError("请选择已采集 Key 或输入完整 Key，二选一")
        return attrs


class CPAKeyListView(CPAAdminView):
    def get(self, request):
        keys = list(
            CPAAPIKey.objects.prefetch_related("bindings__participant").order_by("id")
        )
        known = {key.key_hash for key in keys}
        observed = (
            CPAUsageEvent.objects.exclude(api_key_hash="")
            .exclude(api_key_hash__in=known)
            .order_by()
            .values("api_key_hash", "api_key_hint")
            .distinct()
        )
        return ok(
            {
                "keys": [
                    {
                        "id": key.id,
                        "hint": key.hint,
                        "name": key.name,
                        "observed_hash": key.key_hash,
                        "bindings": [binding_data(row) for row in key.bindings.all()],
                    }
                    for key in keys
                ],
                "unregistered": [
                    {"observed_hash": row["api_key_hash"], "hint": row["api_key_hint"]}
                    for row in observed
                ],
            }
        )

    def post(self, request):
        serializer = BindingWrite(data=request.data)
        serializer.is_valid(raise_exception=True)
        return ok(
            binding_data(bind_key(user=request.user, **serializer.validated_data)), 201
        )


class CPABindingDetailView(CPAAdminView):
    def delete(self, request, binding_id):
        get_object_or_404(CPAKeyBinding, pk=binding_id)
        return ok(binding_data(unbind_key(binding_id)))

    def patch(self, request, binding_id):
        binding = get_object_or_404(CPAKeyBinding, pk=binding_id)
        name = serializers.CharField(max_length=80, allow_blank=True).run_validation(
            request.data.get("name", "")
        )
        binding.key.name = name
        binding.key.save(update_fields=["name"])
        return ok(binding_data(binding))


class ClaimWrite(serializers.Serializer):
    key_id = serializers.PrimaryKeyRelatedField(
        queryset=CPAAPIKey.objects.all(), source="key"
    )
    participant_id = serializers.PrimaryKeyRelatedField(
        queryset=Participant.objects.filter(enabled=True), source="participant"
    )
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField()


class CPAClaimPreviewView(CPAAdminView):
    def post(self, request):
        serializer = ClaimWrite(data=request.data)
        serializer.is_valid(raise_exception=True)
        return ok(
            claim_data(preview_claim(user=request.user, **serializer.validated_data)),
            201,
        )


class CPAClaimApplyView(CPAAdminView):
    def post(self, request, plan_id):
        get_object_or_404(CPAClaimPlan, pk=plan_id)
        return ok(claim_data(apply_claim(plan_id)))


class CPASummaryView(CPAReadView):
    required_page_permissions = (
        PagePermission.DASHBOARD,
        PagePermission.PARTICIPANTS,
        PagePermission.STATISTICS,
    )

    def get(self, request):
        return ok(pool_summary(request.user, cpa_account(request)))


class CPARequestQuery(serializers.Serializer):
    participant_id = serializers.IntegerField(required=False, min_value=1)
    key_id = serializers.IntegerField(required=False, min_value=1)
    model = serializers.CharField(required=False, max_length=255)
    failed = serializers.BooleanField(required=False)
    started_at = serializers.DateTimeField(required=False)
    ended_at = serializers.DateTimeField(required=False)
    page = serializers.IntegerField(default=1, min_value=1)
    page_size = serializers.IntegerField(default=50, min_value=1, max_value=100)
    include_summary = serializers.BooleanField(default=False)
    days = serializers.IntegerField(default=7, min_value=1, max_value=90)

    def validate(self, attrs):
        end = attrs.get("ended_at", timezone.now())
        start = attrs.get("started_at", end - timedelta(days=attrs["days"]))
        if start >= end or end - start > timedelta(days=90):
            raise serializers.ValidationError("请求查询时间范围须大于零且不超过 90 天")
        attrs.update(started_at=start, ended_at=end)
        return attrs


class CPARequestsView(CPAReadView):
    required_page_permissions = (PagePermission.STATISTICS,)

    def get(self, request):
        account = cpa_account(request, enabled_only=False)
        # QueryDict makes DRF treat omitted booleans like unchecked HTML fields.
        # An absent status filter must include both successful and failed requests.
        serializer = CPARequestQuery(data=request.query_params.dict())
        serializer.is_valid(raise_exception=True)
        params = serializer.validated_data
        events = CPAUsageEvent.objects.filter(account=account)
        owned = (
            list(request.user.quota_participants.values_list("id", flat=True))
            if not request.user.is_staff
            else []
        )
        if not request.user.is_staff:
            events = events.filter(ownership_filter(owned))
        if "participant_id" in params:
            if not request.user.is_staff and params["participant_id"] not in owned:
                raise serializers.ValidationError("只能查看本人授权参与者的请求")
            events = events.filter(ownership_filter([params["participant_id"]]))
        # Options are scoped before accepting client-supplied key/model filters.
        key_hashes = events.order_by().values_list("api_key_hash", flat=True).distinct()
        keys = CPAAPIKey.objects.filter(key_hash__in=key_hashes).order_by("id")
        key_options = [
            {"id": key.id, "name": key.name, "hint": key.hint} for key in keys
        ]
        model_options = list(
            events.order_by("model").values_list("model", flat=True).distinct()
        )
        if "key_id" in params:
            key = get_object_or_404(keys, pk=params["key_id"])
            events = events.filter(api_key_hash=key.key_hash)
        events = events.filter(
            occurred_at__gte=params["started_at"], occurred_at__lt=params["ended_at"]
        )
        if params.get("model"):
            events = events.filter(model=params["model"])
        if "failed" in params:
            events = events.filter(failed=params["failed"])
        count = events.count()
        offset = (params["page"] - 1) * params["page_size"]
        config = AppSettings.load()
        rows = []
        for event in events.order_by("-occurred_at", "-id")[
            offset : offset + params["page_size"]
        ]:
            cost, unknown = cpa_event_cost(event, config)
            rows.append(
                {
                    "id": event.id,
                    "occurred_at": event.occurred_at.isoformat(),
                    "request_id": event.request_id,
                    "api_key_hint": event.api_key_hint,
                    "model": event.model,
                    "endpoint": event.endpoint,
                    "input_tokens": event.input_tokens,
                    "cached_input_tokens": event.cached_input_tokens,
                    "output_tokens": event.output_tokens,
                    "reasoning_tokens": event.reasoning_tokens,
                    "total_tokens": event.total_tokens,
                    "failed": event.failed,
                    "latency_ms": event.latency_ms,
                    "ttft_ms": event.ttft_ms,
                    "usage_usd": float(cost),
                    "unpriced": unknown,
                    "requested_service_tier": event.requested_service_tier,
                    "response_service_tier": event.response_service_tier,
                }
            )
        return ok(
            {
                "account_id": account.id,
                "items": rows,
                "total": count,
                "summary": request_summary(events, config) if params["include_summary"] else None,
                "started_at": params["started_at"].isoformat(),
                "ended_at": params["ended_at"].isoformat(),
                "page": params["page"],
                "page_size": params["page_size"],
                "keys": key_options,
                "models": model_options,
                "cost_estimate": True,
                "generated_at": timezone.now().isoformat(),
            }
        )


class ReadOnlyCPASummaryView(CPASummaryView):
    authentication_classes = [APIKeyAuthentication]
    http_method_names = ["get", "head", "options"]


class ReadOnlyCPARequestsView(CPARequestsView):
    authentication_classes = [APIKeyAuthentication]
    http_method_names = ["get", "head", "options"]
