"""Explicit confirmation and server-side owner authorization for quota resets."""

from datetime import timedelta

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from ..access import HasPageAccess, visible_accounts_for
from ..cpa.quota_status import reset_credits
from ..history_state import fenced_fact_write, LeaseGuard
from ..integrations.cpa import CPAClient, CPAError
from ..models import AppSettings, CPAQuotaResetRequest, PagePermission
from .base import AuthenticatedAPIView, ok, error
from .cpa_participants import CPAErrorEnvelope


def can_reset_quota(user, account):
    if not account.enabled:
        return False
    if user.is_staff:
        return True
    owner_ids = list(
        account.pool.allocations.filter(
            participant__enabled=True, participant__is_owner=True
        ).values_list("participant_id", flat=True)
    )
    return (
        len(owner_ids) == 1 and user.quota_participants.filter(pk=owner_ids[0]).exists()
    )


class ResetView(CPAErrorEnvelope, AuthenticatedAPIView):
    permission_classes = [IsAuthenticated, HasPageAccess]
    required_page_permissions = (PagePermission.ACCOUNT_STATUS,)

    def account(self, request, account_id):
        account = get_object_or_404(
            visible_accounts_for(request.user),
            pk=account_id,
            provider="cpa",
            enabled=True,
        )
        if not can_reset_quota(request.user, account):
            raise PermissionDenied("仅管理员或该 CPA 池的车主可重置额度")
        return account


def plan_data(plan, account):
    return {
        "id": str(plan.pk),
        "account_id": account.pk,
        "account_name": account.name,
        "available_count": plan.available_count,
        "status": plan.status,
        "expires_at": plan.expires_at.isoformat(),
    }


class CPAQuotaResetPreviewView(ResetView):
    def post(self, request, account_id):
        account = self.account(request, account_id)
        with fenced_fact_write([account.fact_key]):
            now = timezone.now()
            account.cpa_reset_requests.filter(
                status="pending", expires_at__lte=now
            ).update(status="cancelled")
            existing = (
                account.cpa_reset_requests.filter(status__in=["pending", "unknown"])
                .order_by("created_at")
                .first()
            )
            if existing:
                if (
                    existing.requested_by_id != request.user.pk
                    and not request.user.is_staff
                ):
                    raise ValidationError(
                        "该账号已有待确认或待核对的重置请求，请先处理原请求"
                    )
                return ok(plan_data(existing, account))
            try:
                with CPAClient(AppSettings.load()) as client:
                    _, body = client.query_usage_payload(account.cpa_auth_index)
            except CPAError as exc:
                return error(str(exc), 502)
            count = reset_credits(body)
            if count is None or count <= 0:
                raise ValidationError("没有可靠的可用重置次数，请先刷新额度")
            plan = CPAQuotaResetRequest.objects.create(
                account=account,
                requested_by=request.user,
                source_account_id=account.cpa_auth_index,
                available_count=count,
                expires_at=now + timedelta(minutes=5),
            )
            return ok(plan_data(plan, account), 201)


class CPAQuotaResetConfirmView(ResetView):
    def post(self, request, account_id, plan_id):
        account = self.account(request, account_id)
        if request.data.get("confirmed") is not True:
            raise ValidationError("请先完成重置额度的二次确认")
        guard = LeaseGuard.acquire(account.fact_key)
        try:
            with transaction.atomic():
                guard.assert_owned()
                account.refresh_from_db()
                if (
                    not visible_accounts_for(request.user).filter(pk=account.pk).exists()
                    or not can_reset_quota(request.user, account)
                ):
                    raise PermissionDenied("当前已无重置权限")
                plans = CPAQuotaResetRequest.objects.select_for_update()
                if not request.user.is_staff:
                    plans = plans.filter(requested_by=request.user)
                plan = get_object_or_404(plans, pk=plan_id, account=account)
                if plan.status == "succeeded":
                    return ok(plan_data(plan, account))
                if plan.source_account_id != account.cpa_auth_index:
                    raise ValidationError("账号凭据已变化，不能重试此重置请求")
                if plan.status not in ("pending", "unknown") or (
                    plan.status == "pending" and plan.expires_at <= timezone.now()
                ):
                    raise ValidationError("确认已过期，请重新预览")
                # Commit the uncertainty marker before any upstream mutation.
                # A crash or lost response must reuse this same redemption ID.
                plan.status = "unknown"
                plan.save(update_fields=["status"])
            try:
                with CPAClient(AppSettings.load()) as client:
                    client.consume_reset_credit(account.cpa_auth_index, str(plan.pk))
            except CPAError as exc:
                if getattr(exc, "definitive_rejection", False):
                    with transaction.atomic():
                        guard.assert_owned()
                        plan.status = "failed"
                        plan.finished_at = timezone.now()
                        plan.save(update_fields=["status", "finished_at"])
                    return error(
                        "上游拒绝重置，请检查账号状态和 CPA 版本后重新预览", 502
                    )
                return error(
                    "上游重置结果未确认，请刷新额度核对；重试会复用同一请求标识", 502
                )
            with transaction.atomic():
                guard.assert_owned()
                plan.status = "succeeded"
                plan.finished_at = timezone.now()
                plan.save(update_fields=["status", "finished_at"])
            return ok(plan_data(plan, account))
        finally:
            guard.release()
