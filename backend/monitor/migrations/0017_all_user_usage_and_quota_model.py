from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("monitor", "0016_observation_manual_start")]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="weekly_quota_model",
            field=models.CharField(
                choices=[
                    ("time_varying", "时变额度"),
                    ("constant_average", "平均恒定"),
                ],
                default="time_varying",
                max_length=24,
            ),
        ),
        migrations.CreateModel(
            name="Sub2APIUserUsageSample",
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
                ("account_id", models.BigIntegerField(db_index=True)),
                ("sub2api_user_id", models.BigIntegerField(db_index=True)),
                ("username", models.CharField(blank=True, max_length=150)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("observed_at", models.DateTimeField()),
                ("window_started_at", models.DateTimeField()),
                ("window_resets_at", models.DateTimeField()),
                (
                    "total_standard_cost",
                    models.DecimalField(decimal_places=6, max_digits=18),
                ),
                (
                    "total_actual_cost",
                    models.DecimalField(decimal_places=6, max_digits=18),
                ),
            ],
            options={
                "ordering": ["observed_at", "sub2api_user_id"],
                "indexes": [
                    models.Index(
                        fields=["sub2api_user_id", "observed_at"],
                        name="sub2api_user_usage_time",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "account_id",
                            "sub2api_user_id",
                            "observed_at",
                        ),
                        name="unique_sub2api_user_usage_sample",
                    )
                ],
            },
        ),
    ]
