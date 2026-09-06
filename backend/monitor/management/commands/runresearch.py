"""Independent bounded research worker; disabled installations do no network work."""
import signal
import threading
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from monitor.research.service import run_due


class Command(BaseCommand):
    help = "运行默认关闭的科研共创调度器；不影响主额度监控"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        stopping = threading.Event()
        if not options["once"]:
            signal.signal(signal.SIGTERM, lambda *_: stopping.set())
            signal.signal(signal.SIGINT, lambda *_: stopping.set())
        while not stopping.is_set():
            close_old_connections()
            try:
                result = run_due()
                if result not in ("disabled", "not_due", "busy"):
                    self.stdout.write(f"科研任务状态：{result}")
            except Exception:
                # No raw exception (which may contain local paths/identities).
                self.stderr.write("科研调度暂不可用，稍后重试")
            finally:
                close_old_connections()
            if options["once"]:
                return
            stopping.wait(30)
