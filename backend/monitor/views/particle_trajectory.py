"""粒子滤波历史周期轨迹接口。"""

from .base import AuthenticatedAPIView, error, ok
from ..models import AppSettings
from ..particle_trajectory import particle_trajectory_data


class ParticleTrajectoryView(AuthenticatedAPIView):
    def get(self, request):
        raw_period_id = request.query_params.get("period")
        try:
            period_id = int(raw_period_id) if raw_period_id else None
        except (TypeError, ValueError):
            return error("历史周期参数无效", 400)
        try:
            return ok(
                particle_trajectory_data(
                    AppSettings.load(),
                    period_id=period_id,
                )
            )
        except ValueError as exc:
            return error(str(exc), 400)
