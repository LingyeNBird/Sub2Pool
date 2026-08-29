"""不需要登录的基础 API。"""

from django.conf import settings
from django.utils import timezone

from ..cpa.collector_state import get_collector_status
from .base import PublicAPIView, ok


class HealthView(PublicAPIView):
    def get(self, _request):
        return ok(
            {
                "status": "ok",
                "time": timezone.now().isoformat(),
                "cpa_collector": get_collector_status(include_error=False),
            }
        )


class AuthClientConfigView(PublicAPIView):
    """登录页读取非敏感的 WebRTC 审计配置。"""

    def get(self, _request):
        return ok(
            {
                "webrtc_enabled": settings.WEBRTC_IP_COLLECTION_ENABLED,
                "stun_url": settings.WEBRTC_STUN_URL,
            }
        )
