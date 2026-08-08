"""在进程启动或运维操作后，从原始观测重建全部派生结果。"""

from django.core.management.base import BaseCommand

from monitor.models import Observation
from monitor.replay import rebuild_account


class Command(BaseCommand):
    help = "按账号重放全部原始观测，重建额度折算和参与者归属"

    def handle(self, *args, **options):
        account_ids = list(
            Observation.objects.order_by()
            .values_list("account_id", flat=True)
            .distinct()
        )
        for account_id in account_ids:
            result = rebuild_account(account_id)
            self.stdout.write(
                f"账号 {account_id}：重放 {result.rebuilt_observations} 条观测，"
                f"推断 {result.inferred_intervals} 个区间，"
                f"自动排除 {result.automatic_exclusions} 条"
            )
        self.stdout.write(self.style.SUCCESS("原始观测重放完成"))
