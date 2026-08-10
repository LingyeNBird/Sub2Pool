"""粒子滤波当前周期轨迹接口。"""

from .base import AdminAPIView, ok
from ..models import AppSettings
from ..particle_trajectory import particle_trajectory_data


class ParticleTrajectoryView(AdminAPIView):
    def get(self, _request):
        return ok(particle_trajectory_data(AppSettings.load()))
