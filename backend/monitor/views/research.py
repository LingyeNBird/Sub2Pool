"""Admin-only research consent, aggregate preview and scheduling endpoints."""
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from .base import AdminAPIView, ok, error
from ..models.research import ResearchSettings
from ..research.protocol import POLICY, STUDY, consent_digest, descriptor
from ..research.service import authorized, withdraw
from ..research.transport import normalize_endpoint, destination_ready, DeliveryError


class ConsentSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    projects = serializers.ListField(child=serializers.ChoiceField(choices=[STUDY]), max_length=1)
    endpoint = serializers.CharField(max_length=512, allow_blank=True)
    interval_hours = serializers.IntegerField(min_value=1, max_value=168)
    gateway_only = serializers.BooleanField()
    accept_consent = serializers.BooleanField(default=False)
    policy_version = serializers.CharField(max_length=64, allow_blank=True, default="")

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["科研设置含未知字段"]})
        return super().to_internal_value(data)

    def validate_endpoint(self, value):
        try:
            return normalize_endpoint(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from None

    def validate(self, data):
        if data["enabled"] and not data["projects"]:
            raise serializers.ValidationError("开启科研共创时至少选择一个研究项目")
        return data


def state(config):
    return {
        "enabled": config.enabled, "projects": config.projects, "endpoint": config.endpoint,
        "interval_hours": config.interval_hours, "gateway_only": config.gateway_only,
        "destination_ready": destination_ready(config.endpoint),
        "consent_current": authorized(config), "policy_version": POLICY,
        "last_computed_at": config.last_computed_at, "last_sent_at": config.last_sent_at,
        "next_run_at": config.next_run_at, "last_status": config.last_status, "last_error": config.last_error,
        "can_withdraw": bool(config.last_sent_endpoint), "last_sent_endpoint": config.last_sent_endpoint,
        "summary": config.summary, "method": descriptor(),
        "available_projects": [{"id": STUDY, "title": "GPT-6 额度异常归因"}],
        "privacy": [
            "仅发送滚动 90 天的请求总次数、GPT-6/5.6 次数、取整后的标准成本总额、总额度百分点、有效区间/周期数量和质量计数。",
            "发送固定候选原因的预测评分均值/协方差、重抽样支持度、代表性倍率、方法版本；不发送单条请求、逐区间数据或时间序列。",
            "不发送提示词、回答、Token 明细、账号/参与者名称或 ID、API Key、Sub2API 地址、IP 字段。",
            "使用随机生成、按接收网站隔离的公开密钥标识去重和撤回；属于去标识化分享，不是不可关联的绝对匿名。",
            "直接联网时接收网站及其反向代理仍能看到出口 IP；应用不记录 IP，不代表网络层完全不可见。",
            "关闭后停止启动发送任务；已经发出的请求可能完成。关闭不会自动删除已提交统计，可单独撤回。",
            "只有至少 200 条合格请求才发送；样本不足或不可识别时不展示结论。科研结果不会自动修改计费规则或参与者额度。",
        ],
    }


class ResearchSettingsView(AdminAPIView):
    def get(self, _request):
        return ok(state(ResearchSettings.load()))

    def patch(self, request):
        # A one-click stop must work even if another unsaved form field is bad.
        if request.data == {"enabled": False}:
            with transaction.atomic():
                config = ResearchSettings.objects.select_for_update().get(pk=ResearchSettings.load().pk)
                config.enabled = False
                config.config_revision += 1
                config.next_run_at = config.lease_until = None
                config.lease_token = ""
                config.last_status, config.last_error = "disabled", ""
                config.save()
            return ok(state(config))
        serializer = ConsentSerializer(data=request.data)
        if not serializer.is_valid():
            return error("科研设置无效", 400, serializer.errors)
        values = serializer.validated_data
        digest = consent_digest(values["endpoint"], values["projects"], values["gateway_only"])
        with transaction.atomic():
            config = ResearchSettings.objects.select_for_update().get(pk=ResearchSettings.load().pk)
            if values["enabled"] and (not config.enabled or digest != config.consent_hash):
                if not values["accept_consent"] or values["policy_version"] != POLICY:
                    return error("请先阅读并确认本次发送内容、接收网站和隐私边界", 400)
                config.consent_at = timezone.now()
                config.consent_hash = digest
            for field in ("enabled", "projects", "endpoint", "interval_hours", "gateway_only"):
                setattr(config, field, values[field])
            config.config_revision += 1
            config.lease_token, config.lease_until = "", None
            config.next_run_at = timezone.now() if config.enabled else None
            config.last_status, config.last_error = ("scheduled" if config.enabled else "disabled"), ""
            config.save()
        return ok(state(config))


class ResearchRunView(AdminAPIView):
    def post(self, _request):
        config = ResearchSettings.load()
        if not authorized(config):
            return error("请先开启并确认科研共创授权", 400)
        ResearchSettings.objects.filter(pk=1).update(next_run_at=timezone.now())
        return ok({"scheduled": True, "message": "已排入独立科研进程；接收地址配置完成且满足最小样本量时会按授权发送"}, 202)


class ResearchWithdrawView(AdminAPIView):
    def post(self, request):
        if request.data.get("confirm") is not True:
            return error("撤回会停止后续发送，请明确确认", 400)
        try:
            result = withdraw()
        except DeliveryError as exc:
            return error(str(exc), 502)
        return ok({"status": result})
