"""无 Redis 的轻量后台轮询进程。"""
from datetime import timedelta
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.models import AppSettings


def schedule_next_run(config: AppSettings, *, now=None) -> int:
    """记录全局轮询器下一次唤醒时间，并返回本轮休眠秒数。"""
    sleep_seconds = max(2, config.local_poll_minutes) * 60
    next_run = (now or timezone.now()) + timedelta(seconds=sleep_seconds)
    AppSettings.objects.filter(pk=config.pk).update(next_local_check_at=next_run)
    return sleep_seconds

class Command(BaseCommand):
    help = "按设置页中的本地探测间隔持续运行额度监控"

    def handle(self, *args, **options):
        self.stdout.write("额度监控进程已启动")
        while True:
            close_old_connections()
            config = AppSettings.load()
            try:
                result = run_monitor(force_upstream=False, source="scheduled")
                self.stdout.write(f"监控结果：{result}")
            except Exception as exc:
                # 引擎已经记录错误并按配置发邮件；命令保持运行，等待配置修复或上游恢复。
                self.stderr.write(f"监控失败：{exc}")
            close_old_connections()
            # 重新读取设置，确保本轮结束前修改的间隔会在下一轮生效。
            config = AppSettings.load()
            sleep_seconds = schedule_next_run(config)
            close_old_connections()
            time.sleep(sleep_seconds)
