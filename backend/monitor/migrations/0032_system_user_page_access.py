from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


LEGACY_REGULAR_PAGES = ("participants", "particle_filter", "statistics")


def preserve_legacy_regular_access(apps, _schema_editor):
    user_app, user_model = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(user_app, user_model)
    PageAccess = apps.get_model("monitor", "SystemUserPageAccess")
    rows = [
        PageAccess(user_id=user_id, page_code=page_code)
        for user_id in User.objects.filter(
            is_staff=False,
            is_superuser=False,
        ).values_list("id", flat=True)
        for page_code in LEGACY_REGULAR_PAGES
    ]
    if rows:
        PageAccess.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("monitor", "0031_pooled_participant_contracts"),
    ]

    operations = [
        migrations.CreateModel(
            name="SystemUserPageAccess",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "page_code",
                    models.CharField(
                        choices=[
                            ("dashboard", "额度总览"),
                            ("account_status", "账号状态"),
                            ("participants", "参与者"),
                            ("system_users", "系统用户"),
                            ("observations", "观测记录"),
                            ("particle_filter", "粒子轨迹"),
                            ("statistics", "额度统计"),
                            ("notifications", "通知记录"),
                            ("login_records", "登录记录"),
                            ("settings", "系统设置"),
                            ("tutorial", "使用教程"),
                        ],
                        max_length=32,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="page_accesses",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["user_id", "page_code"]},
        ),
        migrations.AddConstraint(
            model_name="systemuserpageaccess",
            constraint=models.UniqueConstraint(
                fields=("user", "page_code"),
                name="unique_system_user_page_access",
            ),
        ),
        migrations.RunPython(
            preserve_legacy_regular_access,
            migrations.RunPython.noop,
        ),
    ]
