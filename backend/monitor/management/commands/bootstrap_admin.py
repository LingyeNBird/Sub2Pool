import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "根据环境变量幂等创建初始管理员"

    def handle(self, *args, **options):
        username = os.environ.get("ADMIN_USERNAME", "").strip()
        password = os.environ.get("ADMIN_PASSWORD", "")
        email = os.environ.get("ADMIN_EMAIL", "").strip()
        if not username or not password:
            self.stdout.write("未设置 ADMIN_USERNAME/ADMIN_PASSWORD，跳过初始管理员创建")
            return
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"管理员 {username} 已存在")
            return
        try:
            validate_password(password)
        except Exception as exc:
            raise CommandError(f"ADMIN_PASSWORD 不符合密码策略：{exc}") from exc
        User.objects.create_superuser(username=username, password=password, email=email)
        self.stdout.write(self.style.SUCCESS(f"已创建管理员 {username}"))
