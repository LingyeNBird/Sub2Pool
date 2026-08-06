from django.apps import AppConfig


class MonitorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "monitor"
    verbose_name = "拼车额度监控"

    def ready(self) -> None:
        # 注册 SQLite 连接初始化信号。
        from . import signals  # noqa: F401
