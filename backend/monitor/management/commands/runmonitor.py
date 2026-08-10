"""无 Redis 的轻量后台轮询进程。"""
import math
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.api_usage import refresh_due_api_usage_snapshots
from monitor.models import AppSettings


def schedule_next_run(
    config: AppSettings,
    *,
    now=None,
    cycle_started_at=None,
) -> int:
    """记录下一次唤醒时间；按本轮开始时间对齐，避免把执行耗时叠加到间隔。"""
    current = now or timezone.now()
    interval_seconds = max(2, config.local_poll_minutes) * 60
    anchor = cycle_started_at or current
    next_run = anchor + timedelta(seconds=interval_seconds)
    if next_run < current:
        elapsed_seconds = (current - anchor).total_seconds()
        elapsed_intervals = math.ceil(elapsed_seconds / interval_seconds)
        next_run = anchor + timedelta(
            seconds=elapsed_intervals * interval_seconds
        )
    sleep_seconds = math.ceil(max(0, (next_run - current).total_seconds()))
    AppSettings.objects.filter(pk=config.pk).update(next_local_check_at=next_run)
    return sleep_seconds


class Command(BaseCommand):
    help = "按设置页中的本地探测间隔持续运行额度监控"

    def handle(self, *args, **options):
        self.stdout.write("额度监控进程已启动")
        while True:
            close_old_connections()
            config = AppSettings.load()
            cycle_started_at = timezone.now()
            # 任务开始时先登记下一时隙，运行期间前端倒计时也有明确目标。
            schedule_next_run(
                config,
                now=cycle_started_at,
                cycle_started_at=cycle_started_at,
            )
            try:
                result = run_monitor(force_upstream=False, source="scheduled")
                self.stdout.write(f"监控结果：{result}")
            except Exception as exc:
                # 引擎已经记录错误并按配置发邮件；命令保持运行，等待配置修复或上游恢复。
                self.stderr.write(f"监控失败：{exc}")
            else:
                try:
                    api_usage = refresh_due_api_usage_snapshots(
                        AppSettings.load()
                    )
                    if api_usage["refreshed"]:
                        self.stdout.write(
                            "API 用量结论已刷新："
                            f"{api_usage['refreshed']} 名参与者"
                        )
                except Exception as exc:
                    # 附加统计刷新失败不能中断核心额度监控。
                    self.stderr.write(
                        f"API 用量结论刷新失败：{exc}"
                    )
            close_old_connections()
            # 重新读取设置，确保本轮结束前修改的间隔会在下一轮生效。
            # 若执行超过一个间隔，则跳过已错过的时隙，不并发补跑也不额外再等完整间隔。
            config = AppSettings.load()
            sleep_seconds = schedule_next_run(
                config,
                now=timezone.now(),
                cycle_started_at=cycle_started_at,
            )
            close_old_connections()
            time.sleep(sleep_seconds)
