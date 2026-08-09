"""升级后只修复旧算法记录；显式 ``--all`` 才重放全部历史。"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q

from monitor.models import Observation
from monitor.replay import RATE_METHOD, rebuild_account


class Command(BaseCommand):
    help = "按账号修复旧版派生结果；传入 --all 可执行全量运维重放"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="忽略算法版本标记，从账号第一条原始观测开始重放",
        )

    def handle(self, *args, **options):
        account_ids = list(
            Observation.objects.order_by()
            .values_list("account_id", flat=True)
            .distinct()
        )
        for account_id in account_ids:
            replay_from = None
            if not options["all"]:
                stale = (
                    Observation.objects.filter(account_id=account_id)
                    .exclude(exclusion_source="manual")
                    .filter(
                        ~Q(raw_window__rate_method=RATE_METHOD)
                        | Q(sample_note="等待派生计算")
                    )
                    .order_by("observed_at", "id")
                    .first()
                )
                if stale is None:
                    self.stdout.write(f"账号 {account_id}：派生结果已是最新版")
                    continue
                replay_from = (
                    stale.observed_at
                    if stale.is_manual_start
                    else stale.attribution_started_at
                    or stale.upstream_resets_at
                    - timedelta(seconds=stale.window_seconds)
                )

            result = rebuild_account(account_id, replay_from=replay_from)
            self.stdout.write(
                f"账号 {account_id}：重放 {result.rebuilt_observations} 条观测，"
                f"推断 {result.inferred_intervals} 个区间，"
                f"自动排除 {result.automatic_exclusions} 条"
            )
        self.stdout.write(self.style.SUCCESS("原始观测派生结果检查完成"))
