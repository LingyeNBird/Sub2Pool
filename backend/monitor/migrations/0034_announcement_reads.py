from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("monitor", "0033_disable_legacy_fast_correction"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="appsettings",
            name="fast_pricing_upgrade_notice_pending",
        ),
        migrations.CreateModel(
            name="AnnouncementRead",
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
                ("announcement_code", models.CharField(max_length=120)),
                ("read_at", models.DateTimeField()),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="announcement_reads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-read_at"],
                "indexes": [
                    models.Index(
                        fields=["user", "announcement_code"],
                        name="announcement_read_lookup",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "announcement_code"),
                        name="unique_user_announcement_read",
                    )
                ],
            },
        ),
    ]
