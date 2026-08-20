"""粒子滤波历史周期轨迹接口。"""

from .base import PageAccessAPIView, error, ok
from ..models import AppSettings, PagePermission
from .query_params import monitored_account_query
from ..particle_trajectory import particle_trajectory_data


class ParticleTrajectoryView(PageAccessAPIView):
    required_page_permissions = (PagePermission.PARTICLE_FILTER,)

    def get(self, request):
        raw_period_id = request.query_params.get("period")
        try:
            period_id = int(raw_period_id) if raw_period_id else None
            account = monitored_account_query(request)
        except (TypeError, ValueError) as exc:
            return error(str(exc), 400)
        if account is None:
            return ok(
                {
                    "available": False,
                    "message": "尚未配置启用的监控账号",
                }
            )
        try:
            return ok(
                particle_trajectory_data(
                    AppSettings.load(),
                    account,
                    period_id=period_id,
                )
            )
        except ValueError as exc:
            return error(str(exc), 400)
