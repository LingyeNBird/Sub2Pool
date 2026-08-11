"""Persisted FAST correction facts."""
from django.db import models

from .observations import Observation


class ObservationFastCorrection(models.Model):
    """一个观测区间内，单个 Sub2API 用户的 FAST 等效成本修正。

    记录按原始 Sub2API 用户 ID 保存，不依赖当时是否已经创建参与者。因此以后
    才绑定的参与者也能在重放时获得完整的历史 FAST 修正。
    """

    observation = models.ForeignKey(
        Observation,
        on_delete=models.CASCADE,
        related_name="fast_corrections",
    )
    sub2api_user_id = models.BigIntegerField(db_index=True)
    fast_request_count = models.PositiveIntegerField(default=0)
    # 该用户在区间内的全部请求数；FAST 与非 FAST 请求均计入。
    # NULL 表示旧版本明细缺失；只有 verified request_count coverage 才可补齐。
    request_count = models.PositiveIntegerField(null=True, blank=True)
    fast_standard_cost = models.DecimalField(max_digits=18, decimal_places=6)
    fast_actual_cost = models.DecimalField(max_digits=18, decimal_places=6)
    standard_correction_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )
    actual_correction_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )

    class Meta:
        ordering = ["sub2api_user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["observation", "sub2api_user_id"],
                name="unique_observation_fast_user",
            )
        ]
        indexes = [
            models.Index(
                fields=["sub2api_user_id", "observation"],
                name="fast_correction_user_obs",
            )
        ]
