"""FAST 修正事实的数据库持久化。"""

from .domain import FastCorrectionInterval
from ..models import Observation, ObservationFastCorrection


def detail_rows(
    observation: Observation,
    interval: FastCorrectionInterval,
) -> list[ObservationFastCorrection]:
    return [
        ObservationFastCorrection(
            observation=observation,
            sub2api_user_id=row.user_id,
            request_count=row.request_count,
            fast_request_count=row.fast_request_count,
            fast_standard_cost=row.fast_standard_cost,
            fast_actual_cost=row.fast_actual_cost,
            standard_correction_cost=row.standard_correction_cost,
            actual_correction_cost=row.actual_correction_cost,
        )
        for row in interval.users
    ]


def apply_fast_interval(
    observation: Observation,
    interval: FastCorrectionInterval,
) -> None:
    """在调用者的事务中覆盖一个观测区间的可重建 FAST 修正事实。"""

    observation.fast_correction_started_at = interval.started_at
    observation.fast_correction_request_count = interval.request_count
    observation.fast_correction_standard_cost = (
        interval.standard_correction_cost
    )
    observation.fast_correction_actual_cost = interval.actual_correction_cost
    observation.fast_corrections.all().delete()
    rows = detail_rows(observation, interval)
    if rows:
        ObservationFastCorrection.objects.bulk_create(rows, batch_size=500)
