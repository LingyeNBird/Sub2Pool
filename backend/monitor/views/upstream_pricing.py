"""Explicit, administrator-only upstream pricing writes and undo."""
from ..history_state import LeaseBusyError, LeaseLostError
from ..integrations.sub2api import Sub2APIClient, Sub2APIError
from ..models import AppSettings, UpstreamPricingState
from ..upstream_pricing.inspection import current_pricing
from ..upstream_pricing.service import (
    apply_policy,
    pricing_state_payload,
    revert_policy,
)
from .base import AdminAPIView, error, ok


def _pricing_response(state):
    payload = pricing_state_payload(state)
    if state.status in {"failed", "partial"}:
        return error(
            state.last_error or "上游计费操作未全部成功，请查看分组结果。",
            502,
            {"upstream_pricing": payload},
        )
    return ok(payload)


class UpstreamPricingView(AdminAPIView):
    def get(self, request):
        if "group_id" in request.query_params or "kind" in request.query_params:
            kind = request.query_params.get("kind")
            try:
                group_id = int(request.query_params.get("group_id", ""))
                if group_id <= 0 or kind not in {"fast", "model", "context"}:
                    raise ValueError
            except ValueError:
                return error("请选择有效分组与计费类型")
            config = AppSettings.load()
            if not config.sub2api_admin_token_encrypted:
                return error("请先保存 Sub2API 连接配置")
            try:
                with Sub2APIClient(config) as client:
                    return ok(current_pricing(client, UpstreamPricingState.load(), config.sub2api_base_url, group_id, kind))
            except ValueError as exc:
                return error(str(exc), 400)
            except Sub2APIError as exc:
                return error(str(exc), 502)
        if request.query_params.get("groups") == "1":
            config = AppSettings.load()
            if not config.sub2api_admin_token_encrypted:
                return error("请先保存 Sub2API 连接配置")
            try:
                with Sub2APIClient(config) as client:
                    return ok(client.selectable_pricing_groups())
            except Sub2APIError as exc:
                return error(str(exc), 502)
        return ok(pricing_state_payload())


class UpstreamPricingApplyView(AdminAPIView):
    def post(self, request):
        if not isinstance(request.data, dict) or request.data.get("confirm") is not True:
            return error("请确认此次操作将直接修改 Sub2API 分组内所有用户的计费。")
        announcement = request.data.get("announcement", False)
        if not isinstance(announcement, bool):
            return error("公告应用标识必须为布尔值")
        try:
            state = apply_policy(
                request.data.get("policy"), group_ids=request.data.get("group_ids"),
                announcement=announcement,
            )
        except (LeaseBusyError, LeaseLostError) as exc:
            return error(str(exc), 409)
        except ValueError as exc:
            return error(str(exc), 400)
        except Sub2APIError as exc:
            return error(str(exc), 502)
        return _pricing_response(state)


class UpstreamPricingRevertView(AdminAPIView):
    def post(self, request):
        if not isinstance(request.data, dict) or request.data.get("confirm") is not True:
            return error("请确认恢复接管前的上游计费配置；这不会重新开启本地修正。")
        revision = request.data.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            return error("撤回版本无效，请刷新上游计费状态。")
        try:
            state = revert_policy(revision)
        except (LeaseBusyError, LeaseLostError) as exc:
            return error(str(exc), 409)
        except ValueError as exc:
            return error(str(exc), 400)
        except Sub2APIError as exc:
            return error(str(exc), 502)
        return _pricing_response(state)
