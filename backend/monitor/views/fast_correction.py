"""FAST 请求等效成本修正的管理员操作。"""

from rest_framework import serializers

from .base import AdminAPIView, error, ok
from ..fast_correction.rebuild import rebuild_fast_corrections
from ..models import AppSettings
from ..integrations.sub2api import Sub2APIError


class FastCorrectionRebuildSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=("cycle", "all"))


class FastCorrectionRebuildView(AdminAPIView):
    """从选定边界重新读取 Sub2API 日志并重建 FAST 修正。"""

    def post(self, request):
        serializer = FastCorrectionRebuildSerializer(data=request.data)
        if not serializer.is_valid():
            return error("FAST 修正重建范围无效", details=serializer.errors)

        try:
            result = rebuild_fast_corrections(
                AppSettings.load(),
                serializer.validated_data["scope"],
            )
        except ValueError as exc:
            return error(str(exc), 400)
        except Sub2APIError as exc:
            return error(str(exc), 502)
        return ok(result)
