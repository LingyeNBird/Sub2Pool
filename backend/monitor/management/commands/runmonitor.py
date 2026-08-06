"""无 Redis 的轻量后台轮询进程。"""
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from monitor.engine import run_monitor
from monitor.models import AppSettings


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
            time.sleep(max(2, config.local_poll_minutes) * 60)
