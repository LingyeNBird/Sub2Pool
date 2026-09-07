"""Admin-only research consent, aggregate preview and scheduling endpoints."""
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from .base import AdminAPIView, ok, error
from ..models.research import ResearchSettings
from ..research.pooled_protocol import POLICY, STUDY, consent_digest, descriptor
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
            "无请求数、区间数、周期数或本地置信度门槛；一条请求也能贡献，没有对应额度时只贡献规模、不伪造归因信息。",
            "发送每个随机批次的请求/标准成本/额度汇总、质量计数、共同倍率网格的证据曲线、信息矩阵与候选下GPT-6额度份额。原始区间和时间线留在本地。",
            "不采集、不发送、也不在科研分析中使用粒子滤波或平均恒定容量估值；没有估值辅助分析。",
            "不发送提示词、回答、Token明细、账号或参与者名称/ID、API Key、Sub2API地址或IP字段。单条小贡献不承诺最低人数匿名保护。",
            "FAST目标固定2倍、GPT-5.6/6长上下文不额外翻倍，其他模型计费假定正确；只联合研究GPT-6四个分项倍率，不改变运行计费或其他科研。",
            "随机安装公钥和网站隔离批次标识用于替换去重、明确撤回；属于可关联的去标识化分享，不是绝对匿名。网络接收端及反向代理仍可见出口IP。",
            "不同历史批次持续保留，不因120天未更新自动清理。关闭、迁移和更换身份不会自动撤回远端贡献；只有单独确认撤回才发送删除请求。",
            "关闭后不再启动发送；已进入网络的请求可能完成。统计支持度以固定前提和工作模型为条件，不能等同官方计费机制的已校准概率。",

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
