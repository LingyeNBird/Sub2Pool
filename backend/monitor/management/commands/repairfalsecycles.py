"""预检并修复被冻结的错误周期边界；默认只预检，``--apply`` 才写回。

只重放原始观测事实，不修改任何原始采样；修复对象是已经写进
``attribution_started_at`` 的派生周期边界。
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from monitor.accounting.boundaries import same_official_reset
from monitor.history_state import LeaseBusyError, fenced_fact_write
from monitor.models import AppSettings, Observation
from monitor.particle_trajectory import _trajectory_periods
from monitor.replay import rebuild_account


class _Rollback(Exception):
    """预检模式：在事务内抛出，回滚本次重放的全部派生写入。"""


def _summaries(account_id: int) -> list[dict]:
    return [
        {
            "boundary": period["started_at"],
            "ended_at": period["ended_at"],
            "observations": period["observation_count"],
            "used_percent": period["estimated_used_percent"],
        }
        for period in _trajectory_periods(account_id)
    ]


def suspicious_boundaries(account_id: int) -> list[dict]:
    """列出与“首个 0% 观测之后仍属同一周期”相矛盾的已存边界。

    与 ``infer_segments`` 的零值周期确认规则同源：前一观测仍是 0% 且上游
    ``reset_at`` 未发生跨窗口推进时，后一观测必须延续前一区间；它若自成一个
    区间起点，就是被冻结的错误边界。
    """

    observations = list(
        Observation.objects.filter(
            account_id=account_id,
            excluded_at__isnull=True,
            attribution_started_at__isnull=False,
        )
        .order_by("observed_at", "id")
        .values(
            "id",
            "observed_at",
            "attribution_started_at",
            "upstream_resets_at",
            "upstream_used_percent",
            "is_manual_start",
        )
    )
    found: list[dict] = []
    previous = None
    for observation in observations:
        starts_segment = observation["attribution_started_at"] == observation["observed_at"]
        if (
            starts_segment
            and previous is not None
            and not observation["is_manual_start"]
            and previous["upstream_used_percent"] == 0
            and not previous["is_manual_start"]
            and same_official_reset(
                previous["upstream_resets_at"],
                observation["upstream_resets_at"],
            )
        ):
            found.append(
                {
                    "observation_id": observation["id"],
                    "started_at": observation["observed_at"],
                    "previous_id": previous["id"],
                    "previous_observed_at": previous["observed_at"],
                    "reset_gap_seconds": abs(
                        (
                            observation["upstream_resets_at"]
                            - previous["upstream_resets_at"]
                        ).total_seconds()
                    ),
                }
            )
        previous = observation
    return found


def _describe(summary: dict) -> str:
    return (
        f"{summary['boundary']} → {summary['ended_at']}"
        f"（{summary['observations']} 条观测，已用 {summary['used_percent']:.2f}%）"
    )


def _diff(before: list[dict], after: list[dict]) -> list[str]:
    before_by_boundary = {item["boundary"]: item for item in before}
    after_by_boundary = {item["boundary"]: item for item in after}
    lines: list[str] = []
    for boundary, old in before_by_boundary.items():
        new = after_by_boundary.get(boundary)
        if new is None:
            lines.append(f"- 移除：{_describe(old)}")
            continue
        if (new["ended_at"], new["observations"]) != (
            old["ended_at"],
            old["observations"],
        ):
            lines.append(
                f"- 合并：{_describe(old)} ⇒ 结束于 {new['ended_at']}，"
                f"{new['observations']} 条观测，已用 {new['used_percent']:.2f}%"
            )
    for boundary, new in after_by_boundary.items():
        if boundary not in before_by_boundary:
            lines.append(f"- 新增：{_describe(new)}")
    return lines


class Command(BaseCommand):
    help = "预检并修复被冻结的错误周期边界（默认预检，--apply 写回）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--account",
            type=int,
            action="append",
            dest="accounts",
            help="只处理指定账号，可重复；默认处理全部有观测的账号",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="写回重放结果；缺省只在事务内预检并回滚",
        )

    def handle(self, *args, **options):
        account_ids = options["accounts"] or sorted(
            set(Observation.objects.order_by().values_list("account_id", flat=True))
        )
        if not account_ids:
            raise CommandError("没有可处理的账号")
        config = AppSettings.load()
        changed = 0
        for account_id in account_ids:
            if not Observation.objects.filter(account_id=account_id).exists():
                self.stdout.write(f"账号 {account_id}：没有观测，跳过")
                continue
            for item in suspicious_boundaries(account_id):
                self.stdout.write(
                    self.style.WARNING(
                        f"账号 {account_id}：观测 {item['observation_id']} 在 "
                        f"{item['started_at']} 自成一个周期起点，但前一观测 "
                        f"{item['previous_id']}（{item['previous_observed_at']}）"
                        f"仍是 0% 且 reset_at 只差 "
                        f"{item['reset_gap_seconds']:.0f} 秒，属同一官方窗口"
                    )
                )
            before = _summaries(account_id)
            try:
                after, replay = self._replay(account_id, config, apply=options["apply"])
            except LeaseBusyError as error:
                raise CommandError(f"账号 {account_id} 正被其他任务占用：{error}") from error
            diff = _diff(before, after)
            if not diff:
                self.stdout.write(
                    f"账号 {account_id}：{len(before)} 个周期，未发现错误边界，无需修改"
                )
                continue
            changed += 1
            self.stdout.write(
                f"账号 {account_id}：{len(before)} → {len(after)} 个周期，"
                f"重放 {replay['rebuilt_observations']} 条观测"
            )
            for line in diff:
                self.stdout.write(f"  {line}")

        if options["apply"]:
            self.stdout.write(self.style.SUCCESS(f"修复完成，共改动 {changed} 个账号"))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"预检结束（{changed} 个账号需要修复），未写入任何数据；"
                    "确认无误后加 --apply 执行"
                )
            )

    def _replay(self, account_id: int, config: AppSettings, *, apply: bool):
        """一次全量重放：预检时在事务内回滚，应用时提交。"""

        try:
            with transaction.atomic():
                with fenced_fact_write(
                    [account_id],
                    ttl=timedelta(minutes=30),
                ) as guards:
                    replay = rebuild_account(
                        account_id,
                        config,
                        replay_from=None,
                        guard=guards[account_id],
                    )
                after = _summaries(account_id)
                if not apply:
                    raise _Rollback
        except _Rollback:
            pass
        return after, {"rebuilt_observations": replay.rebuilt_observations}
